#ifndef CTRADE_CORE_H
#define CTRADE_CORE_H

#include <string>
#include <vector>
#include <unordered_map>
#include <mutex>
#include <cmath>
#include <algorithm>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include "json.hpp"

struct TickRecord {
    double timestamp;
    double price;
    double volume;
    char side; // 'B' (BID/BUY) or 'A' (ASK/SELL)

    TickRecord() = default;
    TickRecord(double ts, double pr, double vol, char sd)
        : timestamp(ts), price(pr), volume(vol), side(sd) {}
};

struct ValueRecord {
    double timestamp;
    double value;
};

struct OrderbookUnit {
    double bid_price;
    double bid_size;
    double ask_price;
    double ask_size;

    OrderbookUnit() = default;
    OrderbookUnit(double bp, double bs, double ap, double as)
        : bid_price(bp), bid_size(bs), ask_price(ap), ask_size(as) {}
};

struct OrderbookRecord {
    double timestamp = 0.0;
    std::vector<OrderbookUnit> units;
};

struct TimeBucket {
    double bid_vol = 0.0;
    double ask_vol = 0.0;
};

// 고정 크기 링 버퍼
template <typename T, size_t Cap>
class RingBuffer {
private:
    std::vector<T> buffer;
    size_t head = 0;
    size_t tail = 0;
    size_t sz = 0;

public:
    RingBuffer() : buffer(Cap) {}

    void push_back(const T& val) {
        if (sz == Cap) {
            head = (head + 1) % Cap;
            sz--;
        }
        buffer[tail] = val;
        tail = (tail + 1) % Cap;
        sz++;
    }

    size_t size() const { return sz; }
    bool empty() const { return sz == 0; }

    T& operator[](size_t idx) {
        return buffer[(head + idx) % Cap];
    }

    const T& operator[](size_t idx) const {
        return buffer[(head + idx) % Cap];
    }

    void pop_front() {
        if (sz > 0) {
            head = (head + 1) % Cap;
            sz--;
        }
    }

    T& front() {
        return buffer[head];
    }

    const T& front() const {
        return buffer[head];
    }

    void clear() {
        head = 0;
        tail = 0;
        sz = 0;
    }
};

struct WelfordProfile {
    double mean_val = 0.0;
    double var_val = 0.0;
    double std_val = 0.0;

    double mean_tps = 0.0;
    double var_tps = 0.0;
    double std_tps = 0.0;

    double mean_spread = 0.0;
    double var_spread = 0.0;
    double std_spread = 0.0;
};

struct KimchiProfile {
    double kimchi_ema = 0.0;
    bool has_ema = false;
};

class SymbolData {
public:
    std::string symbol;
    
    // 1단계 레이더용 버퍼 (trade_val_window 2만개 제거 ➡️ 240개 double 버킷으로 대체)
    RingBuffer<TickRecord, 3000> tick_window;
    RingBuffer<double, 240> trade_val_buckets;
    double current_val_accum = 0.0;
    double current_val_start_time = 0.0;
    double acc_trade_price_24h = 0.0;
    WelfordProfile welford;
    KimchiProfile kimchi;

    // 2단계 DataManager용 버퍼 및 구조
    std::unordered_map<double, OrderbookRecord> orderbooks;
    RingBuffer<TickRecord, 2000> history;
    RingBuffer<TickRecord, 10000> trades_30s;
    // 3시간 매물대 버킷 구조 (1분 단위 180개 = 3시간; 변수명의 12h 는 레거시 명칭이며 실제 보관 기간은 3시간)
    struct EnergyBucket {
        double start_time = 0.0;
        // 가격 -> {BID_vol, ASK_vol}
        std::unordered_map<double, TimeBucket> energy;
    };
    RingBuffer<EnergyBucket, 180> trades_12h_buckets;
    EnergyBucket current_12h_bucket;

    // 시간별 버킷 관리
    RingBuffer<TimeBucket, 240> time_buckets;
    double current_bucket_bid = 0.0;
    double current_bucket_ask = 0.0;
    double current_bucket_start_time = 0.0;
    double last_update = 0.0;

    SymbolData();
    explicit SymbolData(std::string sym);

    // 1단계 메소드
    void add_tick(double timestamp, double price, double volume, const std::string& side);
    void update_acc_24h(double acc_24h);
    void clean_old_records(double now);
    void update_welford(double total_value, double tps, double curr_spread, bool has_spread, double alpha);
    double update_kimchi(double upbit_price_usdt, double ref_price, double kimchi_ema_alpha);

    // 2단계 메소드
    void update_orderbook(double level, double timestamp, const std::vector<OrderbookUnit>& units_vec);
    void add_history_tick(double timestamp, double price, double volume, const std::string& side, double bucket_size, size_t max_buckets);
    void update_time_buckets(double current_time, double bucket_size, size_t max_buckets);
    pybind11::dict get_analysis_metrics(double level, double current_time, double imbalance_decay_rate, size_t rsi_period, double bucket_size, size_t max_buckets);
    pybind11::dict get_cpp_energy_map(int window_sec, double ref_now);
};

class CoreTracker {
private:
    std::unordered_map<std::string, SymbolData> tracker;
    std::mutex mtx;

public:
    CoreTracker() = default;

    void register_symbol(const std::string& symbol);
    void add_tick(const std::string& symbol, double timestamp, double price, double volume, const std::string& side);
    void update_acc_24h(const std::string& symbol, double acc_24h);
    
    pybind11::dict get_metrics(const std::string& symbol, double now, double usdt_krw, double ref_price, double curr_spread, bool has_spread, double kimchi_ema_alpha, double alpha);
    pybind11::dict get_profile(const std::string& symbol);

    // 2단계 메소드 바인딩
    void update_orderbook(const std::string& symbol, double level, double timestamp, const std::vector<OrderbookUnit>& units_vec);
    void add_history_tick(const std::string& symbol, double timestamp, double price, double volume, const std::string& side, double bucket_size, size_t max_buckets);
    pybind11::dict get_analysis_metrics(const std::string& symbol, double level, double current_time, double imbalance_decay_rate, size_t rsi_period, double bucket_size, size_t max_buckets);
    pybind11::dict get_energy_map(const std::string& symbol, int window_sec, double ref_now);
};

enum class LogicType { AND, OR, NONE };
enum class OpType { GE, GT, LE, LT, EQ, NE };

struct SimpleRule {
    std::string target;
    OpType op = OpType::GE;
    double value = 0.0;
    bool valid = true;  // 알 수 없는 연산자면 false

    bool evaluate(const std::unordered_map<std::string, double>& ctx) const;
};

struct RuleGroup {
    LogicType logic = LogicType::NONE;
    std::vector<SimpleRule> simple_rules;
    std::vector<RuleGroup> nested_groups;

    bool evaluate(const std::unordered_map<std::string, double>& ctx) const;
};

class RuleEvaluator {
private:
    std::string symbol;
    std::string exchange;
    double budget = 1000000.0;
    bool auto_trade = true;
    bool active = true;
    std::string reference_market = "BINANCE";
    double orderbook_level = 0.0;

    RuleGroup buy_group;
    RuleGroup sell_group;

public:
    RuleEvaluator() = default;
    explicit RuleEvaluator(const pybind11::dict& target_config);

    bool should_buy(const pybind11::dict& context) const;
    bool should_sell(const pybind11::dict& context) const;

    // Getters for configuration properties
    std::string get_symbol() const;
    std::string get_exchange() const;
    double get_budget() const;
    bool get_auto_trade() const;
    bool get_active() const;
    std::string get_reference_market() const;
    double get_orderbook_level() const;
};

struct PositionInfo {
    double price = 0.0;
    double qty = 0.0;
    double entry_time = 0.0;
    double max_price = 0.0;
    bool ts_active = false;
};

struct SymbolStats {
    int wins = 0;
    int losses = 0;
    double total_profit = 0.0;
    int trades = 0;
};

struct TradeEvent {
    std::string version;
    std::string type; // "BUY", "SELL", "REFILL"
    std::string symbol;
    double price = 0.0;
    double qty = 0.0;
    double fee = 0.0;
    double amount = 0.0;
    double time = 0.0;
    double balance = 0.0;

    // SELL 전용
    double buy_price = 0.0;
    double sell_price = 0.0;
    double profit = 0.0;
    double profit_rate = 0.0;
    std::string reason;
    double holding_time = 0.0;
    double entry_time = 0.0;
    double exit_time = 0.0;
};

class VirtualTrader {
private:
    double balance;
    double initial_balance;
    double fee_rate;
    bool silent;

    std::unordered_map<std::string, PositionInfo> positions;
    std::unordered_map<std::string, SymbolStats> symbol_stats;
    int total_trades = 0;
    int win_trades = 0;
    std::vector<TradeEvent> history;

public:
    VirtualTrader(double initial_balance = 10000000.0, double fee_rate = 0.0005, bool silent = false);

    bool buy(const std::string& symbol, double price, double amount, double current_time, const std::string& version);
    double sell(const std::string& symbol, double price, const std::string& reason, double current_time, const std::string& version);
    pybind11::dict get_stats(const pybind11::dict& current_prices);

    // Getters / Setters
    double get_balance() const;
    void set_balance(double bal);
    double get_initial_balance() const;
    void set_initial_balance(double init_bal);
    pybind11::dict get_positions() const;
    pybind11::list get_history() const;
    pybind11::dict get_symbol_stats() const;
    int get_total_trades() const;
    int get_win_trades() const;

    // 이력 로드 및 초기화 메소드
    void clear_history();
    void clear_positions();
    void remove_position(const std::string& symbol);
    void reset(double new_initial_balance = -1.0);
    void add_history_event(const pybind11::dict& event);
    void force_set_position(const std::string& symbol, double price, double qty, double entry_time, double max_price, bool ts_active);
};

pybind11::object parse_upbit_message(const std::string& message);
pybind11::object parse_binance_message(const std::string& message, const std::string& market_type);
pybind11::list parse_bitget_message(const std::string& message);
pybind11::object parse_kis_message(const std::string& message, const std::string& symbol);

struct BacktestTick {
    double timestamp;
    double price;
    double volume;
    char side;

    BacktestTick() = default;
    BacktestTick(double ts, double pr, double vol, char sd)
        : timestamp(ts), price(pr), volume(vol), side(sd) {}
};

struct BacktestOrderbook {
    double timestamp;
    double level;
    std::vector<OrderbookUnit> units;

    BacktestOrderbook() = default;
    BacktestOrderbook(double ts, double lvl, const std::vector<OrderbookUnit>& u)
        : timestamp(ts), level(lvl), units(u) {}
};

class Backtester {
private:
    std::vector<BacktestTick> ticks;
    std::vector<BacktestOrderbook> orderbooks;
    
    VirtualTrader trader;
    RuleEvaluator evaluator;
    CoreTracker tracker;

    double budget_per_trade;
    std::string symbol;
    double current_time;

public:
    Backtester(const pybind11::dict& target_config, double initial_balance = 10000000.0, double fee_rate = 0.0005);
    
    void load_ticks(const pybind11::list& py_ticks);
    void load_orderbooks(const pybind11::list& py_orderbooks);
    
    bool load_ticks_from_csv(const std::string& filepath);
    bool load_orderbooks_from_csv(const std::string& filepath);
    
    pybind11::dict run(double warmup_period = 30.0, double reentry_cooldown = 30.0);
};

#endif // CTRADE_CORE_H
