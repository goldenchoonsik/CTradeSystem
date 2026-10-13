#include "ctrade_core.h"

SymbolData::SymbolData() : symbol("") {}

SymbolData::SymbolData(std::string sym) : symbol(std::move(sym)) {}

void SymbolData::add_tick(double timestamp, double price, double volume, const std::string& side) {
    char s = (side == "BID" || side == "BUY" || side == "B") ? 'B' : 'A';
    TickRecord tr{timestamp, price, volume, s};
    tick_window.push_back(tr);

    // 0.25초 거래대금 버킷 누적 처리
    double tick_val = price * volume;
    if (current_val_start_time == 0.0) {
        current_val_start_time = timestamp;
        current_val_accum = tick_val;
    } else {
        double elapsed = timestamp - current_val_start_time;
        if (elapsed >= 0.25) {
            trade_val_buckets.push_back(current_val_accum);
            
            // 빈 버킷 처리 (거래 소강 상태일 때 0으로 채움)
            int num_empty = static_cast<int>(elapsed / 0.25) - 1;
            for (int i = 0; i < num_empty; ++i) {
                trade_val_buckets.push_back(0.0);
            }
            
            current_val_accum = tick_val;
            current_val_start_time = timestamp - fmod(elapsed, 0.25);
        } else {
            current_val_accum += tick_val;
        }
    }
}

void SymbolData::update_acc_24h(double acc_24h) {
    acc_trade_price_24h = acc_24h;
}

void SymbolData::clean_old_records(double now) {
    while (tick_window.size() > 0 && (now - tick_window.front().timestamp) > 10.0) {
        tick_window.pop_front();
    }
}

void SymbolData::update_welford(double total_value, double tps, double curr_spread, bool has_spread, double alpha) {
    double diff_val = total_value - welford.mean_val;
    welford.mean_val += alpha * diff_val;
    welford.var_val = (1.0 - alpha) * (welford.var_val + alpha * diff_val * diff_val);
    welford.std_val = std::sqrt(std::max(1e-9, welford.var_val));

    double diff_tps = tps - welford.mean_tps;
    welford.mean_tps += alpha * diff_tps;
    welford.var_tps = (1.0 - alpha) * (welford.var_tps + alpha * diff_tps * diff_tps);
    welford.std_tps = std::sqrt(std::max(1e-9, welford.var_tps));

    if (has_spread) {
        double diff_spread = curr_spread - welford.mean_spread;
        welford.mean_spread += alpha * diff_spread;
        welford.var_spread = (1.0 - alpha) * (welford.var_spread + alpha * diff_spread * diff_spread);
        welford.std_spread = std::sqrt(std::max(1e-9, welford.var_spread));
    }
}

double SymbolData::update_kimchi(double upbit_price_usdt, double ref_price, double kimchi_ema_alpha) {
    if (upbit_price_usdt <= 0.0 || ref_price <= 0.0) {
        return 0.0;
    }
    double curr_kimchi = ((upbit_price_usdt / ref_price) - 1.0) * 100.0;
    if (!kimchi.has_ema) {
        kimchi.kimchi_ema = curr_kimchi;
        kimchi.has_ema = true;
    } else {
        kimchi.kimchi_ema = kimchi.kimchi_ema + kimchi_ema_alpha * (curr_kimchi - kimchi.kimchi_ema);
    }
    double kimchi_delta = curr_kimchi - kimchi.kimchi_ema;
    return kimchi_delta;
}

// 2단계 메소드 구현
void SymbolData::update_orderbook(double level, double timestamp, const std::vector<OrderbookUnit>& units_vec) {
    orderbooks[level] = OrderbookRecord{timestamp, units_vec};
}

void SymbolData::add_history_tick(double timestamp, double price, double volume, const std::string& side, double bucket_size, size_t max_buckets) {
    char s = (side == "BID" || side == "BUY" || side == "B") ? 'B' : 'A';
    TickRecord tr{timestamp, price, volume, s};

    // 3시간(180분) 매물대 1분 단위 버킷 누적 처리
    if (current_12h_bucket.start_time == 0.0) {
        current_12h_bucket.start_time = timestamp;
    }
    double elapsed_12h = timestamp - current_12h_bucket.start_time;
    if (elapsed_12h >= 60.0) {
        trades_12h_buckets.push_back(current_12h_bucket);
        current_12h_bucket = EnergyBucket();
        current_12h_bucket.start_time = timestamp - fmod(elapsed_12h, 60.0);
    }
    auto& tb = current_12h_bucket.energy[price];
    if (s == 'B') {
        tb.bid_vol += volume;
    } else {
        tb.ask_vol += volume;
    }

    history.push_back(tr);
    trades_30s.push_back(tr);

    // 시간 버킷 관리
    update_time_buckets(timestamp, bucket_size, max_buckets);
    if (s == 'B') {
        current_bucket_bid += volume;
    } else if (s == 'A') {
        current_bucket_ask += volume;
    }

    // 30초 이전 데이터 삭제
    while (trades_30s.size() > 0 && (timestamp - trades_30s.front().timestamp) > 30.0) {
        trades_30s.pop_front();
    }

    last_update = timestamp;
}

void SymbolData::update_time_buckets(double current_time, double bucket_size, size_t max_buckets) {
    if (bucket_size <= 0.0) return;
    if (current_bucket_start_time == 0.0) {
        current_bucket_start_time = current_time;
        return;
    }

    // 링 버퍼 용량(240) 고정 -> 호출자가 넘긴 max_buckets 를 [1, 240] 으로 보정
    const size_t kRingCap = 240;
    size_t cap = std::min(std::max<size_t>(max_buckets, 1), kRingCap);

    double elapsed = current_time - current_bucket_start_time;
    if (elapsed >= bucket_size) {
        time_buckets.push_back(TimeBucket{current_bucket_bid, current_bucket_ask});

        // 장시간 무틱 뒤 빈 버킷 채우기: 윈도우(cap)를 넘는 만큼은 의미 없으므로 상한 적용
        double n_empty_d = std::floor(elapsed / bucket_size) - 1.0;
        size_t num_empty = (n_empty_d <= 0.0) ? 0 : (n_empty_d > (double)cap ? cap : (size_t)n_empty_d);
        for (size_t i = 0; i < num_empty; ++i) {
            time_buckets.push_back(TimeBucket{0.0, 0.0});
        }
        // max_buckets < 240 인 경우 윈도우 초과분 제거
        while (time_buckets.size() > cap) {
            time_buckets.pop_front();
        }

        current_bucket_bid = 0.0;
        current_bucket_ask = 0.0;
        current_bucket_start_time = current_time - fmod(elapsed, bucket_size);
    }
}

pybind11::dict SymbolData::get_analysis_metrics(double level, double current_time, double imbalance_decay_rate, size_t rsi_period, double bucket_size, size_t max_buckets) {
    pybind11::dict res;

    // 1. 호가 불균형 (Weighted Imbalance)
    double imbalance = 0.0;
    double bid_sum = 0.0;
    double ask_sum = 0.0;
    double density_ratio = 1.0;

    auto it = orderbooks.find(level);
    if (it != orderbooks.end() && !it->second.units.empty()) {
        const auto& units = it->second.units;
        for (size_t i = 0; i < units.size(); ++i) {
            double weight = std::pow(imbalance_decay_rate, i);
            bid_sum += units[i].bid_size * weight;
            ask_sum += units[i].ask_size * weight;
        }

        if ((bid_sum + ask_sum) > 0.0) {
            imbalance = (bid_sum - ask_sum) / (bid_sum + ask_sum) * 100.0;
        }

        // 구간 밀도 분석 (Price Range Density - ±0.1%)
        double current_price = units[0].bid_price;
        if (current_price > 0.0) {
            double bid_range_limit = current_price * 0.999;
            double ask_range_limit = current_price * 1.001;
            double bid_density = 0.0;
            double ask_density = 0.0;

            for (const auto& u : units) {
                if (u.bid_price >= bid_range_limit) bid_density += u.bid_size;
                if (u.ask_price <= ask_range_limit) ask_density += u.ask_size;
            }
            density_ratio = (ask_density > 0.0) ? (bid_density / ask_density) : 999.0;
        }
    }

    // 2. 체결 강도 (Trade Intensity)
    double intensity = 100.0;
    if (history.size() >= 100) {
        double buy_vol = 0.0;
        double sell_vol = 0.0;
        for (size_t i = 0; i < history.size(); ++i) {
            if (history[i].side == 'B') buy_vol += history[i].volume;
            else if (history[i].side == 'A') sell_vol += history[i].volume;
        }
        if (sell_vol > 0.0) {
            intensity = (buy_vol / sell_vol) * 100.0;
        } else if (buy_vol > 0.0) {
            intensity = 999999.0;
        }
    }

    // 3. 시간 기반 체결 강도 (Time-based Intensity)
    update_time_buckets(current_time, bucket_size, max_buckets);
    double t_buy_vol = 0.0;
    double t_sell_vol = 0.0;
    for (size_t i = 0; i < time_buckets.size(); ++i) {
        t_buy_vol += time_buckets[i].bid_vol;
        t_sell_vol += time_buckets[i].ask_vol;
    }
    double time_intensity = 100.0;
    if (time_buckets.size() > 0) {
        if (t_sell_vol > 0.0) time_intensity = (t_buy_vol / t_sell_vol) * 100.0;
        else if (t_buy_vol > 0.0) time_intensity = 999.0;
    }

    // 4. VWAP
    double total_pv = 0.0;
    double total_v = 0.0;
    for (size_t i = 0; i < history.size(); ++i) {
        total_pv += history[i].price * history[i].volume;
        total_v += history[i].volume;
    }
    double vwap = (total_v > 0.0) ? (total_pv / total_v) : 0.0;

    // 5. RSI (단기 모멘텀)
    double rsi = 50.0;
    if (history.size() >= rsi_period) {
        std::vector<double> prices;
        size_t h_sz = history.size();
        for (size_t i = h_sz - rsi_period; i < h_sz; ++i) {
            prices.push_back(history[i].price);
        }
        double up = 0.0;
        double down = 0.0;
        for (size_t i = 1; i < prices.size(); ++i) {
            double diff = prices[i] - prices[i-1];
            if (diff > 0.0) up += diff;
            else down += std::abs(diff);
        }
        if ((up + down) > 0.0) rsi = (up / (up + down)) * 100.0;
    }

    // 최근 5초간 틱 발생 횟수
    size_t activity_5s = 0;
    for (size_t i = 0; i < history.size(); ++i) {
        if (current_time - history[i].timestamp <= 5.0) {
            activity_5s++;
        }
    }

    // 매도벽 저항지수 (ask_wall_resistance)
    double ask_wall_resistance = 0.0;
    if (it != orderbooks.end() && !it->second.units.empty() && history.size() > 0) {
        double curr_p = history[history.size() - 1].price;
        if (curr_p > 0.0) {
            double wall_limit = curr_p * 1.002;
            double ask_wall_qty = 0.0;
            for (const auto& u : it->second.units) {
                if (u.ask_price <= wall_limit) {
                    ask_wall_qty += u.ask_size;
                }
            }
            double avg_trade_qty_30s = 0.0;
            if (trades_30s.size() > 0) {
                double sum_v = 0.0;
                for (size_t i = 0; i < trades_30s.size(); ++i) sum_v += trades_30s[i].volume;
                avg_trade_qty_30s = sum_v / trades_30s.size();
            }
            if (avg_trade_qty_30s > 0.0) {
                ask_wall_resistance = ask_wall_qty / avg_trade_qty_30s;
            }
        }
    }

    // 최근 10초 순매수 누적 (cumulative_delta_10s)
    double cumulative_delta_10s = 0.0;
    double buy_val_10s = 0.0;
    double sell_val_10s = 0.0;
    for (size_t i = 0; i < trades_30s.size(); ++i) {
        const auto& t = trades_30s[i];
        if (current_time - t.timestamp <= 10.0) {
            if (t.side == 'B') buy_val_10s += t.volume * t.price;
            else if (t.side == 'A') sell_val_10s += t.volume * t.price;
        }
    }
    cumulative_delta_10s = buy_val_10s - sell_val_10s;

    // 최근 5틱 연속 상승 비율 (up_tick_ratio_5t)
    double up_tick_ratio_5t = 0.5;
    if (history.size() >= 2) {
        size_t h_sz = history.size();
        size_t count = std::min(h_sz, static_cast<size_t>(6));
        size_t up_ticks = 0;
        for (size_t i = h_sz - count + 1; i < h_sz; ++i) {
            if (history[i].price > history[i-1].price) up_ticks++;
        }
        up_tick_ratio_5t = static_cast<double>(up_ticks) / (count - 1);
    }

    res["imbalance"] = imbalance;
    res["density_ratio"] = density_ratio;
    res["intensity"] = intensity;
    res["time_intensity"] = time_intensity;
    res["vwap"] = vwap;
    res["rsi"] = rsi;
    res["activity_5s"] = activity_5s;
    res["ask_wall_resistance"] = ask_wall_resistance;
    res["cumulative_delta_10s"] = cumulative_delta_10s;
    res["up_tick_ratio_5t"] = up_tick_ratio_5t;

    return res;
}

pybind11::dict SymbolData::get_cpp_energy_map(int window_sec, double ref_now) {
    pybind11::dict res;
    // 30초 이하의 윈도우는 trades_30s 활용, 30초 초과는 trades_12h 활용
    if (window_sec <= 30) {
        for (size_t i = 0; i < trades_30s.size(); ++i) {
            const auto& t = trades_30s[i];
            if (ref_now - t.timestamp <= window_sec) {
                pybind11::object price_key = pybind11::cast(t.price);
                pybind11::dict sub;
                if (res.contains(price_key)) {
                    sub = res[price_key];
                } else {
                    sub["BID"] = 0.0;
                    sub["ASK"] = 0.0;
                }
                std::string t_side = (t.side == 'B') ? "BID" : "ASK";
                sub[pybind11::cast(t_side)] = sub[pybind11::cast(t_side)].cast<double>() + t.volume;
                res[price_key] = sub;
            }
        }
    } else {
        // trades_12h_buckets 순회하여 집계
        size_t bucket_count = trades_12h_buckets.size();
        for (size_t i = 0; i < bucket_count; ++i) {
            const auto& bucket = trades_12h_buckets[i];
            if (ref_now - bucket.start_time <= window_sec) {
                for (const auto& kv : bucket.energy) {
                    double price_val = kv.first;
                    const auto& tb = kv.second;
                    pybind11::object price_key = pybind11::cast(price_val);
                    pybind11::dict sub;
                    if (res.contains(price_key)) {
                        sub = res[price_key];
                    } else {
                        sub["BID"] = 0.0;
                        sub["ASK"] = 0.0;
                    }
                    sub["BID"] = sub["BID"].cast<double>() + tb.bid_vol;
                    sub["ASK"] = sub["ASK"].cast<double>() + tb.ask_vol;
                    res[price_key] = sub;
                }
            }
        }
        // 현재 수집 중인 12H 버킷의 값도 더해줍니다.
        if (ref_now - current_12h_bucket.start_time <= window_sec) {
            for (const auto& kv : current_12h_bucket.energy) {
                double price_val = kv.first;
                const auto& tb = kv.second;
                pybind11::object price_key = pybind11::cast(price_val);
                pybind11::dict sub;
                if (res.contains(price_key)) {
                    sub = res[price_key];
                } else {
                    sub["BID"] = 0.0;
                    sub["ASK"] = 0.0;
                }
                sub["BID"] = sub["BID"].cast<double>() + tb.bid_vol;
                sub["ASK"] = sub["ASK"].cast<double>() + tb.ask_vol;
                res[price_key] = sub;
            }
        }
    }
    return res;
}

void CoreTracker::register_symbol(const std::string& symbol) {
    std::lock_guard<std::mutex> lock(mtx);
    if (tracker.find(symbol) == tracker.end()) {
        tracker[symbol] = SymbolData(symbol);
    }
}

void CoreTracker::add_tick(const std::string& symbol, double timestamp, double price, double volume, const std::string& side) {
    std::lock_guard<std::mutex> lock(mtx);
    auto it = tracker.find(symbol);
    if (it != tracker.end()) {
        it->second.add_tick(timestamp, price, volume, side);
    }
}

void CoreTracker::update_acc_24h(const std::string& symbol, double acc_24h) {
    std::lock_guard<std::mutex> lock(mtx);
    auto it = tracker.find(symbol);
    if (it != tracker.end()) {
        it->second.update_acc_24h(acc_24h);
    }
}

pybind11::dict CoreTracker::get_metrics(const std::string& symbol, double now, double usdt_krw, double ref_price, double curr_spread, bool has_spread, double kimchi_ema_alpha, double alpha) {
    std::lock_guard<std::mutex> lock(mtx);
    pybind11::dict res;

    auto it = tracker.find(symbol);
    if (it == tracker.end()) {
        return res;
    }

    SymbolData& sd = it->second;
    sd.clean_old_records(now);

    double total_value = 0.0;
    double buy_value = 0.0;
    size_t tick_count = sd.tick_window.size();

    for (size_t i = 0; i < tick_count; ++i) {
        const auto& tr = sd.tick_window[i];
        double val = tr.price * tr.volume;
        total_value += val;
        if (tr.side == 'B') {
            buy_value += val;
        }
    }

    double tps = tick_count / 10.0;
    double vbp = (total_value > 0.0) ? (buy_value / total_value * 100.0) : 50.0;

    double avg_10s_24h = (sd.acc_trade_price_24h / 86400.0) * 10.0;
    double rvol = (avg_10s_24h > 0.0) ? (total_value / avg_10s_24h * 100.0) : 0.0;

    double trade_val_1m_total = 0.0;
    size_t val_count = sd.trade_val_buckets.size();
    for (size_t i = 0; i < val_count; ++i) {
        trade_val_1m_total += sd.trade_val_buckets[i];
    }
    // 현재 수집 중인 미완료 버킷 거래대금도 더해줍니다.
    trade_val_1m_total += sd.current_val_accum;

    double p_now = (tick_count > 0) ? sd.tick_window[tick_count - 1].price : 0.0;
    double kimchi_delta = 0.0;
    if (p_now > 0.0 && usdt_krw > 0.0) {
        double upbit_price_usdt = p_now / usdt_krw;
        kimchi_delta = sd.update_kimchi(upbit_price_usdt, ref_price, kimchi_ema_alpha);
    }

    sd.update_welford(total_value, tps, curr_spread, has_spread, alpha);

    double p_3s = -1.0;
    for (int i = static_cast<int>(tick_count) - 1; i >= 0; --i) {
        if (now - sd.tick_window[i].timestamp >= 3.0) {
            p_3s = sd.tick_window[i].price;
            break;
        }
    }

    res["price"] = p_now;
    res["total_value"] = total_value;
    res["vbp"] = vbp;
    res["tps"] = tps;
    res["rvol"] = rvol;
    res["trade_val_1m"] = trade_val_1m_total;
    res["kimchi_delta"] = kimchi_delta;
    res["p_3s"] = p_3s;

    res["mean_val"] = sd.welford.mean_val;
    res["var_val"] = sd.welford.var_val;
    res["std_val"] = sd.welford.std_val;

    res["mean_tps"] = sd.welford.mean_tps;
    res["var_tps"] = sd.welford.var_tps;
    res["std_tps"] = sd.welford.std_tps;

    res["mean_spread"] = sd.welford.mean_spread;
    res["var_spread"] = sd.welford.var_spread;
    res["std_spread"] = sd.welford.std_spread;

    return res;
}

pybind11::dict CoreTracker::get_profile(const std::string& symbol) {
    std::lock_guard<std::mutex> lock(mtx);
    pybind11::dict res;

    auto it = tracker.find(symbol);
    if (it == tracker.end()) {
        return res;
    }

    const SymbolData& sd = it->second;
    res["mean_val"] = sd.welford.mean_val;
    res["var_val"] = sd.welford.var_val;
    res["std_val"] = sd.welford.std_val;

    res["mean_tps"] = sd.welford.mean_tps;
    res["var_tps"] = sd.welford.var_tps;
    res["std_tps"] = sd.welford.std_tps;

    res["mean_spread"] = sd.welford.mean_spread;
    res["var_spread"] = sd.welford.var_spread;
    res["std_spread"] = sd.welford.std_spread;

    return res;
}

// 2단계 메소드 바인딩 구현
void CoreTracker::update_orderbook(const std::string& symbol, double level, double timestamp, const std::vector<OrderbookUnit>& units_vec) {
    std::lock_guard<std::mutex> lock(mtx);
    auto it = tracker.find(symbol);
    if (it != tracker.end()) {
        it->second.update_orderbook(level, timestamp, units_vec);
    }
}

void CoreTracker::add_history_tick(const std::string& symbol, double timestamp, double price, double volume, const std::string& side, double bucket_size, size_t max_buckets) {
    std::lock_guard<std::mutex> lock(mtx);
    auto it = tracker.find(symbol);
    if (it != tracker.end()) {
        it->second.add_history_tick(timestamp, price, volume, side, bucket_size, max_buckets);
    }
}

pybind11::dict CoreTracker::get_analysis_metrics(const std::string& symbol, double level, double current_time, double imbalance_decay_rate, size_t rsi_period, double bucket_size, size_t max_buckets) {
    std::lock_guard<std::mutex> lock(mtx);
    auto it = tracker.find(symbol);
    if (it != tracker.end()) {
        return it->second.get_analysis_metrics(level, current_time, imbalance_decay_rate, rsi_period, bucket_size, max_buckets);
    }
    return pybind11::dict();
}

pybind11::dict CoreTracker::get_energy_map(const std::string& symbol, int window_sec, double ref_now) {
    std::lock_guard<std::mutex> lock(mtx);
    auto it = tracker.find(symbol);
    if (it != tracker.end()) {
        return it->second.get_cpp_energy_map(window_sec, ref_now);
    }
    return pybind11::dict();
}

namespace py = pybind11;

// ==========================================
// 3단계: Rule Engine & Compiler C++ 구현부
// ==========================================

bool SimpleRule::evaluate(const std::unordered_map<std::string, double>& ctx) const {
    if (!valid) return false;  // 알 수 없는 연산자 룰은 항상 거짓 (fail-closed)
    auto it = ctx.find(target);
    // ctx 에 키가 없으면 0.0 으로 간주하지 않고 거짓 (예: '<' 룰이 부재 값에 대해 참이 되는 것 방지)
    if (it == ctx.end()) return false;
    double val = it->second;
    switch (op) {
        case OpType::GE: return val >= value;
        case OpType::GT: return val > value;
        case OpType::LE: return val <= value;
        case OpType::LT: return val < value;
        case OpType::EQ: return val == value;
        case OpType::NE: return val != value;
    }
    return false;
}

bool RuleGroup::evaluate(const std::unordered_map<std::string, double>& ctx) const {
    // 빈 그룹은 거짓 (Python RuleCompiler 와 동일; 빈 매수조건이 무조건 매수가 되는 것 방지)
    if (simple_rules.empty() && nested_groups.empty()) return false;
    if (logic == LogicType::AND) {
        for (const auto& r : simple_rules) {
            if (!r.evaluate(ctx)) return false;
        }
        for (const auto& g : nested_groups) {
            if (!g.evaluate(ctx)) return false;
        }
        return true;
    } else if (logic == LogicType::OR) {
        for (const auto& r : simple_rules) {
            if (r.evaluate(ctx)) return true;
        }
        for (const auto& g : nested_groups) {
            if (g.evaluate(ctx)) return true;
        }
        return false;
    }
    return false;
}

RuleGroup parse_conditions(const pybind11::dict& cond) {
    RuleGroup group;
    std::string logic = "AND";
    if (cond.contains("logic")) {
        logic = cond["logic"].cast<std::string>();
    }
    group.logic = (logic == "OR") ? LogicType::OR : LogicType::AND;

    if (cond.contains("rules")) {
        pybind11::list rules = cond["rules"].cast<pybind11::list>();
        for (auto item : rules) {
            pybind11::dict r = item.cast<pybind11::dict>();
            if (r.contains("rules")) {
                group.nested_groups.push_back(parse_conditions(r));
            } else {
                SimpleRule rule;
                rule.target = r["target"].cast<std::string>();
                std::string op_str = r["operator"].cast<std::string>();
                if (op_str == ">=") rule.op = OpType::GE;
                else if (op_str == ">") rule.op = OpType::GT;
                else if (op_str == "<=") rule.op = OpType::LE;
                else if (op_str == "<") rule.op = OpType::LT;
                else if (op_str == "==") rule.op = OpType::EQ;
                else if (op_str == "!=") rule.op = OpType::NE;
                else rule.valid = false;
                rule.value = r["value"].cast<double>();
                group.simple_rules.push_back(rule);
            }
        }
    }
    return group;
}

RuleEvaluator::RuleEvaluator(const pybind11::dict& target_config) {
    symbol = target_config["symbol"].cast<std::string>();
    exchange = target_config["exchange"].cast<std::string>();
    budget = target_config.contains("budget_per_trade") ? target_config["budget_per_trade"].cast<double>() : 1000000.0;
    auto_trade = target_config.contains("auto_trade") ? target_config["auto_trade"].cast<bool>() : true;
    active = target_config.contains("active") ? target_config["active"].cast<bool>() : true;
    reference_market = target_config.contains("reference_market") ? target_config["reference_market"].cast<std::string>() : "BINANCE";
    orderbook_level = target_config.contains("orderbook_level") ? target_config["orderbook_level"].cast<double>() : 0.0;

    if (target_config.contains("buy_conditions")) {
        buy_group = parse_conditions(target_config["buy_conditions"].cast<pybind11::dict>());
    }
    if (target_config.contains("sell_conditions")) {
        sell_group = parse_conditions(target_config["sell_conditions"].cast<pybind11::dict>());
    }
}

bool RuleEvaluator::should_buy(const pybind11::dict& context) const {
    std::unordered_map<std::string, double> ctx;
    for (auto item : context) {
        ctx[item.first.cast<std::string>()] = item.second.cast<double>();
    }
    return buy_group.evaluate(ctx);
}

bool RuleEvaluator::should_sell(const pybind11::dict& context) const {
    std::unordered_map<std::string, double> ctx;
    for (auto item : context) {
        ctx[item.first.cast<std::string>()] = item.second.cast<double>();
    }
    return sell_group.evaluate(ctx);
}

std::string RuleEvaluator::get_symbol() const { return symbol; }
std::string RuleEvaluator::get_exchange() const { return exchange; }
double RuleEvaluator::get_budget() const { return budget; }
bool RuleEvaluator::get_auto_trade() const { return auto_trade; }
bool RuleEvaluator::get_active() const { return active; }
std::string RuleEvaluator::get_reference_market() const { return reference_market; }
double RuleEvaluator::get_orderbook_level() const { return orderbook_level; }

// ==========================================
// 4단계: VirtualTrader C++ 구현부
// ==========================================

static bool is_stock_symbol_cpp(const std::string& symbol) {
    if (symbol.empty() || symbol == "SYSTEM") return false;
    if (symbol.length() == 6 && std::all_of(symbol.begin(), symbol.end(), ::isdigit)) {
        return true;
    }
    if (symbol.find('-') == std::string::npos &&
        symbol.find('_') == std::string::npos &&
        symbol.find("USDT") == std::string::npos &&
        symbol.find("USDC") == std::string::npos &&
        symbol.find("BUSD") == std::string::npos) {
        return true;
    }
    return false;
}

VirtualTrader::VirtualTrader(double initial_balance, double fee_rate, bool silent)
    : balance(initial_balance), initial_balance(initial_balance), fee_rate(fee_rate), silent(silent) {}

bool VirtualTrader::buy(const std::string& symbol, double price, double amount, double current_time, const std::string& version) {
    if (amount < 5000.0) return false;

    // 잔고 자동 수혈 (시뮬레이션 전용)
    if (balance < amount) {
        double refill_amount = 50000000.0;
        balance += refill_amount;
        
        TradeEvent refill_event;
        refill_event.version = version;
        refill_event.type = "REFILL";
        refill_event.symbol = "SYSTEM";
        refill_event.price = 0.0;
        refill_event.qty = 0.0;
        refill_event.fee = 0.0;
        refill_event.amount = refill_amount;
        refill_event.time = current_time;
        refill_event.balance = balance;
        
        history.push_back(refill_event);
        if (history.size() > 3000) {
            history.erase(history.begin());
        }
    }

    if (balance >= amount) {
        bool is_stock = is_stock_symbol_cpp(symbol);
        double net_investment = amount / (1.0 + fee_rate);
        double qty = 0.0;

        if (is_stock) {
            // 주식/ETF: 1주 단위 정수 버림수(floor) 적용 (소수점 매수 방지)
            qty = std::floor(net_investment / price);
            if (qty < 1.0) return false; // 1주 미만 매수 불가
        } else {
            // 암호화폐: 소수점 8자리 정밀도
            qty = std::round((net_investment / price) * 100000000.0) / 100000000.0;
            if (qty <= 0.0) return false;
        }

        double actual_net = qty * price;
        double total_cost = actual_net * (1.0 + fee_rate);

        if (balance < total_cost) {
            if (is_stock) {
                qty = std::floor((balance / (1.0 + fee_rate)) / price);
                if (qty < 1.0) return false;
                actual_net = qty * price;
                total_cost = actual_net * (1.0 + fee_rate);
            } else {
                total_cost = amount;
                actual_net = amount / (1.0 + fee_rate);
                qty = std::round((actual_net / price) * 100000000.0) / 100000000.0;
            }
        }

        balance -= total_cost;

        auto it = positions.find(symbol);
        if (it != positions.end()) {
            double total_qty = it->second.qty + qty;
            double avg_price = (it->second.price * it->second.qty + price * qty) / total_qty;
            it->second.price = avg_price;
            it->second.qty = total_qty;
            // 최초 진입 시간 유지
        } else {
            PositionInfo pos;
            pos.price = price;
            pos.qty = qty;
            pos.entry_time = current_time;
            pos.max_price = price;
            pos.ts_active = false;
            positions[symbol] = pos;
        }

        total_trades++;

        TradeEvent buy_event;
        buy_event.version = version;
        buy_event.type = "BUY";
        buy_event.symbol = symbol;
        buy_event.price = price;
        buy_event.qty = qty;
        buy_event.fee = total_cost - actual_net;
        buy_event.amount = total_cost;
        buy_event.time = current_time;
        buy_event.balance = balance;

        history.push_back(buy_event);
        if (history.size() > 3000) {
            history.erase(history.begin());
        }
        return true;
    }
    return false;
}

double VirtualTrader::sell(const std::string& symbol, double price, const std::string& reason, double current_time, const std::string& version) {
    auto it = positions.find(symbol);
    if (it == positions.end()) return -999.0; // 실패 신호

    PositionInfo pos = it->second;
    positions.erase(it);

    double revenue = (pos.qty * price) * (1.0 - fee_rate);
    double cost = (pos.qty * pos.price) * (1.0 + fee_rate);
    double profit = revenue - cost;
    double profit_rate = (profit / cost) * 100.0;

    double holding_time = current_time - pos.entry_time;
    balance += revenue;

    if (profit > 0.0) {
        win_trades++;
    }

    TradeEvent sell_event;
    sell_event.version = version;
    sell_event.type = "SELL";
    sell_event.symbol = symbol;
    sell_event.buy_price = pos.price;
    sell_event.sell_price = price;
    sell_event.qty = pos.qty;
    sell_event.fee = (pos.qty * price) * fee_rate;
    sell_event.amount = revenue;
    sell_event.profit = profit;
    sell_event.profit_rate = profit_rate;
    sell_event.reason = reason;
    sell_event.holding_time = holding_time;
    sell_event.entry_time = pos.entry_time;
    sell_event.exit_time = current_time;
    sell_event.balance = balance;

    history.push_back(sell_event);
    if (history.size() > 3000) {
        history.erase(history.begin());
    }

    // 종목별 통계 갱신
    auto s_it = symbol_stats.find(symbol);
    if (s_it == symbol_stats.end()) {
        SymbolStats stats;
        symbol_stats[symbol] = stats;
        s_it = symbol_stats.find(symbol);
    }
    s_it->second.trades++;
    s_it->second.total_profit += profit;
    if (profit > 0.0) {
        s_it->second.wins++;
    } else {
        s_it->second.losses++;
    }

    return profit;
}

pybind11::dict VirtualTrader::get_stats(const pybind11::dict& current_prices) {
    double unrealized_pnl = 0.0;
    double pos_market_value = 0.0;

    for (const auto& pair : positions) {
        double curr_price = pair.second.price;
        if (current_prices.contains(pair.first.c_str())) {
            curr_price = current_prices[pair.first.c_str()].cast<double>();
        }
        double revenue = (pair.second.qty * curr_price) * (1.0 - fee_rate);
        double cost = (pair.second.qty * pair.second.price) * (1.0 + fee_rate);
        unrealized_pnl += (revenue - cost);

        // L-3: 청산 수수료를 차감한 순 평가액으로 총자산 산출 (unrealized_pnl 과 일관)
        pos_market_value += revenue;
    }

    double total_asset = balance + pos_market_value;
    double total_roi = ((total_asset / initial_balance) - 1.0) * 100.0;
    double win_rate = (total_trades > 0) ? (static_cast<double>(win_trades) / total_trades * 100.0) : 0.0;

    pybind11::dict res;
    res["balance"] = balance;
    res["total_asset"] = total_asset;
    res["total_roi"] = total_roi;
    res["win_rate"] = win_rate;
    res["total_trades"] = total_trades;
    res["active_positions"] = positions.size();

    // 파이썬 연동용 데이터 구조 변환
    res["positions"] = get_positions();
    res["history"] = get_history();
    res["symbol_stats"] = get_symbol_stats();
    return res;
}

double VirtualTrader::get_balance() const { return balance; }
void VirtualTrader::set_balance(double bal) { balance = bal; }
double VirtualTrader::get_initial_balance() const { return initial_balance; }
void VirtualTrader::set_initial_balance(double init_bal) {
    if (init_bal > 0.0) {
        initial_balance = init_bal;
    }
}

void VirtualTrader::clear_positions() {
    positions.clear();
}

void VirtualTrader::remove_position(const std::string& symbol) {
    positions.erase(symbol);
}

void VirtualTrader::reset(double new_initial_balance) {
    if (new_initial_balance > 0.0) {
        initial_balance = new_initial_balance;
    }
    balance = initial_balance;
    positions.clear();
    history.clear();
    symbol_stats.clear();
    total_trades = 0;
    win_trades = 0;
}

pybind11::dict VirtualTrader::get_positions() const {
    pybind11::dict res;
    for (const auto& pair : positions) {
        pybind11::dict pos;
        pos["price"] = pair.second.price;
        pos["qty"] = pair.second.qty;
        pos["time"] = pair.second.entry_time;
        pos["max_price"] = pair.second.max_price;
        pos["ts_active"] = pair.second.ts_active;
        res[pair.first.c_str()] = pos;
    }
    return res;
}

pybind11::list VirtualTrader::get_history() const {
    pybind11::list res;
    for (const auto& ev : history) {
        pybind11::dict item;
        item["version"] = ev.version;
        item["type"] = ev.type;
        item["symbol"] = ev.symbol;
        item["price"] = ev.price;
        item["qty"] = ev.qty;
        item["fee"] = ev.fee;
        item["amount"] = ev.amount;
        item["time"] = ev.time;
        item["balance"] = ev.balance;

        if (ev.type == "SELL") {
            item["buy_price"] = ev.buy_price;
            item["sell_price"] = ev.sell_price;
            item["qty"] = ev.qty;
            item["fee"] = ev.fee;
            item["amount"] = ev.amount;
            item["profit"] = ev.profit;
            item["profit_rate"] = ev.profit_rate;
            item["reason"] = ev.reason;
            item["holding_time"] = ev.holding_time;
            item["entry_time"] = ev.entry_time;
            item["exit_time"] = ev.exit_time;
        }
        res.append(item);
    }
    return res;
}

pybind11::dict VirtualTrader::get_symbol_stats() const {
    pybind11::dict res;
    for (const auto& pair : symbol_stats) {
        pybind11::dict stats;
        stats["wins"] = pair.second.wins;
        stats["losses"] = pair.second.losses;
        stats["total_profit"] = pair.second.total_profit;
        stats["trades"] = pair.second.trades;
        res[pair.first.c_str()] = stats;
    }
    return res;
}

int VirtualTrader::get_total_trades() const { return total_trades; }
int VirtualTrader::get_win_trades() const { return win_trades; }

void VirtualTrader::clear_history() {
    history.clear();
    total_trades = 0;
    win_trades = 0;
    symbol_stats.clear();
}

void VirtualTrader::add_history_event(const pybind11::dict& ev) {
    TradeEvent event;
    event.version = ev.contains("version") ? ev["version"].cast<std::string>() : "";
    event.type = ev.contains("type") ? ev["type"].cast<std::string>() : "";
    event.symbol = ev.contains("symbol") ? ev["symbol"].cast<std::string>() : "";
    event.price = ev.contains("price") ? ev["price"].cast<double>() : 0.0;
    event.qty = ev.contains("qty") ? ev["qty"].cast<double>() : 0.0;
    event.fee = ev.contains("fee") ? ev["fee"].cast<double>() : 0.0;
    event.amount = ev.contains("amount") ? ev["amount"].cast<double>() : 0.0;
    event.time = ev.contains("time") ? ev["time"].cast<double>() : 0.0;
    event.balance = ev.contains("balance") ? ev["balance"].cast<double>() : 0.0;

    if (ev.contains("buy_price")) event.buy_price = ev["buy_price"].cast<double>();
    if (ev.contains("sell_price")) event.sell_price = ev["sell_price"].cast<double>();
    if (ev.contains("profit")) event.profit = ev["profit"].cast<double>();
    if (ev.contains("profit_rate")) event.profit_rate = ev["profit_rate"].cast<double>();
    if (ev.contains("reason")) event.reason = ev["reason"].cast<std::string>();
    if (ev.contains("holding_time")) event.holding_time = ev["holding_time"].cast<double>();
    if (ev.contains("entry_time")) event.entry_time = ev["entry_time"].cast<double>();
    if (ev.contains("exit_time")) event.exit_time = ev["exit_time"].cast<double>();

    history.push_back(event);
    if (history.size() > 3000) {
        history.erase(history.begin());
    }

    if (event.type == "SELL") {
        total_trades++;
        if (event.profit > 0.0) {
            win_trades++;
        }
        auto s_it = symbol_stats.find(event.symbol);
        if (s_it == symbol_stats.end()) {
            SymbolStats stats;
            symbol_stats[event.symbol] = stats;
            s_it = symbol_stats.find(event.symbol);
        }
        s_it->second.trades++;
        s_it->second.total_profit += event.profit;
        if (event.profit > 0.0) {
            s_it->second.wins++;
        } else {
            s_it->second.losses++;
        }
    }
}

void VirtualTrader::force_set_position(const std::string& symbol, double price, double qty, double entry_time, double max_price, bool ts_active) {
    PositionInfo pos;
    pos.price = price;
    pos.qty = qty;
    pos.entry_time = entry_time;
    pos.max_price = max_price;
    pos.ts_active = ts_active;
    positions[symbol] = pos;
}

#include <chrono>
#include <sstream>
#include <algorithm>

using json = nlohmann::json;

static py::object get_tick_data_class() {
    static py::object cls = py::module_::import("core.models").attr("TickData");
    return cls;
}

static py::object get_orderbook_unit_class() {
    static py::object cls = py::module_::import("core.models").attr("OrderbookUnit");
    return cls;
}

static py::object get_orderbook_data_class() {
    static py::object cls = py::module_::import("core.models").attr("OrderbookData");
    return cls;
}

static double get_current_time_sec() {
    auto now = std::chrono::system_clock::now();
    auto duration = now.time_since_epoch();
    return std::chrono::duration_cast<std::chrono::microseconds>(duration).count() / 1000000.0;
}

static std::vector<std::string> split_str(const std::string& str, char delim) {
    std::vector<std::string> tokens;
    std::string token;
    std::istringstream tokenStream(str);
    while (std::getline(tokenStream, token, delim)) {
        tokens.push_back(token);
    }
    return tokens;
}

py::object parse_upbit_message(const std::string& message) {
    try {
        auto j = json::parse(message);
        if (!j.contains("type")) return py::none();
        
        std::string m_type = j["type"].get<std::string>();
        if (m_type == "ticker" || m_type == "trade") {
            std::string symbol = j.value("code", "");
            double price = j.value("trade_price", 0.0);
            double volume = j.value("trade_volume", 0.0);
            std::string side = j.value("ask_bid", "BID");
            double ts = get_current_time_sec();
            
            return get_tick_data_class()(symbol, "UPBIT", price, volume, side, ts);
        } else if (m_type == "orderbook") {
            std::string raw_symbol = j.contains("code") ? j["code"].get<std::string>() : (j.contains("market") ? j["market"].get<std::string>() : "");
            
            std::string symbol = raw_symbol;
            double level = 0.0;
            size_t dot_pos = raw_symbol.find('.');
            if (dot_pos != std::string::npos) {
                symbol = raw_symbol.substr(0, dot_pos);
                try {
                    level = std::stod(raw_symbol.substr(dot_pos + 1));
                } catch (...) {
                    level = 0.0;
                }
            }
            
            py::list units_list;
            if (j.contains("orderbook_units")) {
                for (const auto& u : j["orderbook_units"]) {
                    double bp = u.value("bid_price", 0.0);
                    double bs = u.value("bid_size", 0.0);
                    double ap = u.value("ask_price", 0.0);
                    double as = u.value("ask_size", 0.0);
                    units_list.append(get_orderbook_unit_class()(bp, bs, ap, as));
                }
            }
            double ts = get_current_time_sec();
            return get_orderbook_data_class()(symbol, "UPBIT", units_list, ts, level);
        }
    } catch (...) {
        // parsing error
    }
    return py::none();
}

py::object parse_binance_message(const std::string& message, const std::string& market_type) {
    try {
        auto j = json::parse(message);
        if (j.contains("stream") && j.contains("data")) {
            std::string stream_name = j["stream"].get<std::string>();
            auto payload = j["data"];
            
            size_t at_pos = stream_name.find('@');
            std::string symbol = (at_pos != std::string::npos) ? stream_name.substr(0, at_pos) : "";
            std::transform(symbol.begin(), symbol.end(), symbol.begin(), ::toupper);
            
            if (market_type == "FUTURES") {
                symbol += "_PERP";
            }
            
            std::string exchange = (market_type == "SPOT") ? "BINANCE" : "BINANCE_FUTURES";
            
            if (stream_name.find("@trade") != std::string::npos) {
                std::string side = payload.value("m", false) ? "ASK" : "BID";
                
                double price = 0.0;
                double volume = 0.0;
                if (payload.contains("p")) {
                    if (payload["p"].is_string()) {
                        price = std::stod(payload["p"].get<std::string>());
                    } else {
                        price = payload["p"].get<double>();
                    }
                }
                if (payload.contains("q")) {
                    if (payload["q"].is_string()) {
                        volume = std::stod(payload["q"].get<std::string>());
                    } else {
                        volume = payload["q"].get<double>();
                    }
                }
                
                double ts = get_current_time_sec();
                if (payload.contains("T")) {
                    ts = payload["T"].get<double>() / 1000.0;
                }
                
                return get_tick_data_class()(symbol, exchange, price, volume, side, ts);
            } else if (stream_name.find("@depth") != std::string::npos) {
                py::list units_list;
                // 현물 partial depth: bids/asks, 선물 partial depth: b/a (둘 다 수용)
                const char* bk = payload.contains("bids") ? "bids" : (payload.contains("b") ? "b" : nullptr);
                const char* ak = payload.contains("asks") ? "asks" : (payload.contains("a") ? "a" : nullptr);
                if (bk && ak) {
                    auto bids = payload[bk];
                    auto asks = payload[ak];
                    size_t limit = std::min({(size_t)5, bids.size(), asks.size()});
                    for (size_t i = 0; i < limit; ++i) {
                        double bp = bids[i][0].is_string() ? std::stod(bids[i][0].get<std::string>()) : bids[i][0].get<double>();
                        double bs = bids[i][1].is_string() ? std::stod(bids[i][1].get<std::string>()) : bids[i][1].get<double>();
                        double ap = asks[i][0].is_string() ? std::stod(asks[i][0].get<std::string>()) : asks[i][0].get<double>();
                        double as = asks[i][1].is_string() ? std::stod(asks[i][1].get<std::string>()) : asks[i][1].get<double>();
                        units_list.append(get_orderbook_unit_class()(bp, bs, ap, as));
                    }
                }
                double ts = get_current_time_sec();
                return get_orderbook_data_class()(symbol, exchange, units_list, ts, 0.0);
            }
        }
    } catch (...) {
        // parsing error
    }
    return py::none();
}

py::list parse_bitget_message(const std::string& message) {
    py::list results;
    try {
        auto j = json::parse(message);
        if (j.contains("event")) return results;
        
        if (j.contains("action") && j.contains("data") && j.contains("arg")) {
            auto arg = j["arg"];
            auto payload = j["data"];
            
            std::string channel = arg.value("channel", "");
            std::string instType = arg.value("instType", "");
            std::string instId = arg.value("instId", "");
            
            std::string market_type = (instType == "USDT-FUTURES") ? "FUTURES" : "SPOT";
            std::string symbol = (market_type == "FUTURES") ? (instId + "_BPERP") : instId;
            std::string exchange = (market_type == "SPOT") ? "BITGET" : "BITGET_FUTURES";
            
            for (const auto& item : payload) {
                if (channel == "trade") {
                    std::string side_str = item.value("side", "buy");
                    std::transform(side_str.begin(), side_str.end(), side_str.begin(), ::tolower);
                    std::string side = (side_str == "buy") ? "BID" : "ASK";
                    
                    double ts = get_current_time_sec();
                    if (item.contains("ts")) {
                        if (item["ts"].is_string()) {
                            ts = std::stod(item["ts"].get<std::string>()) / 1000.0;
                        } else {
                            ts = item["ts"].get<double>() / 1000.0;
                        }
                    }
                    
                    double price = 0.0;
                    double volume = 0.0;
                    if (item.contains("price")) {
                        price = item["price"].is_string() ? std::stod(item["price"].get<std::string>()) : item["price"].get<double>();
                    }
                    if (item.contains("size")) {
                        volume = item["size"].is_string() ? std::stod(item["size"].get<std::string>()) : item["size"].get<double>();
                    }
                    
                    results.append(get_tick_data_class()(symbol, exchange, price, volume, side, ts));
                } else if (channel.rfind("books", 0) == 0) {
                    double ts = get_current_time_sec();
                    if (item.contains("ts")) {
                        if (item["ts"].is_string()) {
                            ts = std::stod(item["ts"].get<std::string>()) / 1000.0;
                        } else {
                            ts = item["ts"].get<double>() / 1000.0;
                        }
                    }
                    
                    py::list units_list;
                    if (item.contains("bids") && item.contains("asks")) {
                        auto bids = item["bids"];
                        auto asks = item["asks"];
                        size_t limit = std::min({(size_t)5, bids.size(), asks.size()});
                        for (size_t i = 0; i < limit; ++i) {
                            double bp = bids[i][0].is_string() ? std::stod(bids[i][0].get<std::string>()) : bids[i][0].get<double>();
                            double bs = bids[i][1].is_string() ? std::stod(bids[i][1].get<std::string>()) : bids[i][1].get<double>();
                            double ap = asks[i][0].is_string() ? std::stod(asks[i][0].get<std::string>()) : asks[i][0].get<double>();
                            double as = asks[i][1].is_string() ? std::stod(asks[i][1].get<std::string>()) : asks[i][1].get<double>();
                            units_list.append(get_orderbook_unit_class()(bp, bs, ap, as));
                        }
                    }
                    results.append(get_orderbook_data_class()(symbol, exchange, units_list, ts, 0.0));
                }
            }
        }
    } catch (...) {
        // parsing error
    }
    return results;
}

// 종목코드 위치 자동 감지 헬퍼
static size_t detect_symbol_offset(const std::vector<std::string>& fields, const std::string& symbol) {
    if (fields.empty()) return 0;
    // 앞 5개 필드에서 종목코드를 검색 (다양한 형식 대응)
    for (size_t i = 0; i < fields.size() && i < 5; ++i) {
        if (fields[i] == symbol) return i;
    }
    // 종목코드 미발견 시: 첫 필드가 빈 문자열이면 표준 형식 가정 (offset=1)
    if (fields[0].empty()) return 1;
    return 0;
}

py::object parse_kis_message(const std::string& message, const std::string& symbol) {
    try {
        std::vector<std::string> parts = split_str(message, '|');
        if (parts.size() < 4) return py::none();
        
        std::string tr_id = parts[1];
        std::string data_body = parts[3];
        std::vector<std::string> fields = split_str(data_body, '^');
        if (fields.empty()) return py::none();
        
        size_t offset = detect_symbol_offset(fields, symbol);
        
        if (tr_id == "H0STCNT0") {
            if (fields.size() <= offset + 12) return py::none();
            
            double price = std::stod(fields[offset + 2]);
            double ask1 = fields[offset + 10].empty() ? 0.0 : std::stod(fields[offset + 10]);
            double bid1 = fields[offset + 11].empty() ? 0.0 : std::stod(fields[offset + 11]);
            double volume = std::stod(fields[offset + 12]);
            
            std::string side = "BID";
            if (price <= bid1 && price < ask1) {
                side = "ASK";
            }
            double ts = get_current_time_sec();
            return get_tick_data_class()(symbol, "KIS", price, volume, side, ts);
        } else if (tr_id == "H0STASP0") {
            if (fields.size() < offset + 43) return py::none();
            
            py::list units_list;
            for (size_t i = 0; i < 10; ++i) {
                double ap = std::stod(fields[offset + 3 + i]);
                double bp = std::stod(fields[offset + 13 + i]);
                double as = std::stod(fields[offset + 23 + i]);
                double bs = std::stod(fields[offset + 33 + i]);
                units_list.append(get_orderbook_unit_class()(bp, bs, ap, as));
            }
            double ts = get_current_time_sec();
            return get_orderbook_data_class()(symbol, "KIS", units_list, ts, 0.0);
        }
    } catch (...) {
        // parsing error
    }
    return py::none();
}

Backtester::Backtester(const pybind11::dict& target_config, double initial_balance, double fee_rate)
    : trader(initial_balance, fee_rate, true), evaluator(target_config), current_time(0.0) {
    symbol = target_config["symbol"].cast<std::string>();
    budget_per_trade = target_config.contains("budget_per_trade") ? target_config["budget_per_trade"].cast<double>() : 1000000.0;
    tracker.register_symbol(symbol);
}

void Backtester::load_ticks(const pybind11::list& py_ticks) {
    ticks.clear();
    for (auto item : py_ticks) {
        py::object t = item.cast<py::object>();
        double ts = t.attr("timestamp").cast<double>();
        double price = t.attr("price").cast<double>();
        double vol = t.attr("volume").cast<double>();
        std::string side_str = t.attr("side").cast<std::string>();
        char side = (side_str == "BID" || side_str == "BUY") ? 'B' : 'A';
        ticks.push_back(BacktestTick(ts, price, vol, side));
    }
}

void Backtester::load_orderbooks(const pybind11::list& py_orderbooks) {
    orderbooks.clear();
    for (auto item : py_orderbooks) {
        py::object o = item.cast<py::object>();
        double ts = o.attr("timestamp").cast<double>();
        double lvl = o.attr("level").cast<double>();
        py::list units_list = o.attr("units").cast<py::list>();
        std::vector<OrderbookUnit> units;
        for (auto u_item : units_list) {
            py::object u = u_item.cast<py::object>();
            double bp = u.attr("bid_price").cast<double>();
            double bs = u.attr("bid_size").cast<double>();
            double ap = u.attr("ask_price").cast<double>();
            double as = u.attr("ask_size").cast<double>();
            units.push_back(OrderbookUnit(bp, bs, ap, as));
        }
        orderbooks.push_back(BacktestOrderbook(ts, lvl, units));
    }
}

#include <fstream>

bool Backtester::load_ticks_from_csv(const std::string& filepath) {
    std::ifstream file(filepath);
    if (!file.is_open()) return false;
    ticks.clear();
    std::string line;
    // skip header if any
    std::getline(file, line);
    while (std::getline(file, line)) {
        if (line.empty()) continue;
        auto fields = split_str(line, ',');
        if (fields.size() < 4) continue;
        try {
            double ts = std::stod(fields[0]);
            double price = std::stod(fields[1]);
            double vol = std::stod(fields[2]);
            std::string side_str = fields[3];
            char side = (side_str == "BID" || side_str == "BUY" || side_str == "B") ? 'B' : 'A';
            ticks.push_back(BacktestTick(ts, price, vol, side));
        } catch(...) {
            continue;
        }
    }
    return true;
}

bool Backtester::load_orderbooks_from_csv(const std::string& filepath) {
    std::ifstream file(filepath);
    if (!file.is_open()) return false;
    orderbooks.clear();
    std::string line;
    std::getline(file, line);
    while (std::getline(file, line)) {
        if (line.empty()) continue;
        auto fields = split_str(line, ',');
        if (fields.size() < 6) continue;
        try {
            double ts = std::stod(fields[0]);
            double lvl = std::stod(fields[1]);
            std::vector<OrderbookUnit> units;
            for (size_t i = 2; i + 3 < fields.size(); i += 4) {
                double bp = std::stod(fields[i]);
                double bs = std::stod(fields[i+1]);
                double ap = std::stod(fields[i+2]);
                double as = std::stod(fields[i+3]);
                units.push_back(OrderbookUnit(bp, bs, ap, as));
            }
            orderbooks.push_back(BacktestOrderbook(ts, lvl, units));
        } catch(...) {
            continue;
        }
    }
    return true;
}

struct BacktestEventInfo {
    double timestamp;
    bool is_tick;
    size_t index;
};

pybind11::dict Backtester::run(double warmup_period, double reentry_cooldown) {
    std::vector<BacktestEventInfo> events;
    events.reserve(ticks.size() + orderbooks.size());
    for (size_t i = 0; i < ticks.size(); ++i) {
        events.push_back({ticks[i].timestamp, true, i});
    }
    for (size_t i = 0; i < orderbooks.size(); ++i) {
        events.push_back({orderbooks[i].timestamp, false, i});
    }
    std::sort(events.begin(), events.end(), [](const BacktestEventInfo& a, const BacktestEventInfo& b) {
        if (a.timestamp == b.timestamp) {
            return !a.is_tick && b.is_tick;
        }
        return a.timestamp < b.timestamp;
    });

    double start_time = events.empty() ? 0.0 : events[0].timestamp;
    double last_exit_time = 0.0;
    double orderbook_level = evaluator.get_orderbook_level();

    for (const auto& ev : events) {
        current_time = ev.timestamp;
        
        if (!ev.is_tick) {
            const auto& ob = orderbooks[ev.index];
            tracker.update_orderbook(symbol, ob.level, ob.timestamp, ob.units);
        } else {
            const auto& t = ticks[ev.index];
            
            const std::string side_str = (t.side == 'B') ? "BID" : "ASK";
            tracker.add_history_tick(symbol, t.timestamp, t.price, t.volume, side_str, 0.5, 240);
            tracker.add_tick(symbol, t.timestamp, t.price, t.volume, side_str);
            
            if (t.timestamp - start_time < warmup_period) {
                continue;
            }
            
            py::dict analysis = tracker.get_analysis_metrics(symbol, orderbook_level, t.timestamp, 0.9, 14, 0.5, 240);
            
            py::dict ctx;
            ctx["local_intensity"] = analysis.contains("intensity") ? analysis["intensity"] : py::cast(100.0);
            ctx["local_time_intensity"] = analysis.contains("time_intensity") ? analysis["time_intensity"] : py::cast(100.0);
            ctx["local_imbalance"] = analysis.contains("imbalance") ? analysis["imbalance"] : py::cast(0.0);
            ctx["local_rsi"] = analysis.contains("rsi") ? analysis["rsi"] : py::cast(50.0);
            {
                double vwap_v = analysis.contains("vwap") ? analysis["vwap"].cast<double>() : 0.0;
                ctx["price_vs_vwap"] = (vwap_v > 0.0) ? ((t.price / vwap_v) - 1.0) * 100.0 : 0.0;
            }
            ctx["cumulative_delta_10s"] = analysis.contains("cumulative_delta_10s") ? analysis["cumulative_delta_10s"] : py::cast(0.0);
            ctx["up_tick_ratio_5t"] = analysis.contains("up_tick_ratio_5t") ? analysis["up_tick_ratio_5t"] : py::cast(0.5);
            ctx["ask_wall_resistance"] = analysis.contains("ask_wall_resistance") ? analysis["ask_wall_resistance"] : py::cast(0.0);
            
            py::dict positions = trader.get_positions();
            bool has_position = positions.contains(symbol.c_str());
            
            if (has_position) {
                py::dict pos = positions[symbol.c_str()].cast<py::dict>();
                double entry_price = pos["price"].cast<double>();
                double qty = pos["qty"].cast<double>();
                double entry_time = pos["time"].cast<double>();
                double max_price = pos.contains("max_price") ? pos["max_price"].cast<double>() : entry_price;
                bool ts_active = pos.contains("ts_active") ? pos["ts_active"].cast<bool>() : false;
                
                double profit_rate = ((t.price / entry_price) - 1.0) * 100.0;
                ctx["profit_rate"] = profit_rate;
                
                max_price = std::max(max_price, t.price);
                double max_profit_rate = ((max_price / entry_price) - 1.0) * 100.0;
                if (profit_rate >= 0.3) {
                    ts_active = true;
                }
                
                bool is_ts_triggered = false;
                if (ts_active && profit_rate <= max_profit_rate - 0.10) {
                    is_ts_triggered = true;
                }
                
                double holding_time = t.timestamp - entry_time;
                bool is_momentum_exit_triggered = false;
                if (holding_time >= 5.0 && holding_time <= 10.0 && profit_rate < 0.05) {
                    is_momentum_exit_triggered = true;
                }
                
                trader.force_set_position(symbol, entry_price, qty, entry_time, max_price, ts_active);
                
                std::string reason = "";
                if (evaluator.should_sell(ctx)) {
                    reason = (profit_rate > 0.0) ? "PROFIT" : "LOSS";
                } else if (is_ts_triggered) {
                    reason = "TRAILING_STOP";
                } else if (is_momentum_exit_triggered) {
                    reason = "5S_MOMENTUM_EXIT";
                } else {
                    double timeout = 120.0;
                    double fee_rate = 0.0005;
                    double breakeven_p = entry_price * (1.0 + fee_rate * 2.0);
                    if (holding_time > timeout && t.price >= breakeven_p) {
                        reason = "TIMEOUT";
                    }
                }
                
                if (!reason.empty()) {
                    trader.sell(symbol, t.price, reason, t.timestamp, "v1.3.0-backtest");
                    last_exit_time = t.timestamp;
                }
            } else {
                if (t.timestamp - last_exit_time >= reentry_cooldown) {
                    ctx["profit_rate"] = 0.0;
                    if (evaluator.should_buy(ctx)) {
                        trader.buy(symbol, t.price, budget_per_trade, t.timestamp, "v1.3.0-backtest");
                    }
                }
            }
        }
    }
    return trader.get_stats(py::dict());
}

PYBIND11_MODULE(ctrade_core, m) {
    m.doc() = "ctrade_core C++ extension for HFT indicators";

    m.def("parse_upbit_message", &parse_upbit_message);
    m.def("parse_binance_message", &parse_binance_message);
    m.def("parse_bitget_message", &parse_bitget_message);
    m.def("parse_kis_message", &parse_kis_message);

    py::class_<TickRecord>(m, "TickRecord")
        .def(py::init<double, double, double, char>())
        .def_readwrite("timestamp", &TickRecord::timestamp)
        .def_readwrite("price", &TickRecord::price)
        .def_readwrite("volume", &TickRecord::volume)
        .def_property("side", 
            [](const TickRecord& self) { return std::string(1, self.side); },
            [](TickRecord& self, const std::string& side) { self.side = side.empty() ? 'B' : side[0]; });

    py::class_<OrderbookUnit>(m, "OrderbookUnit")
        .def(py::init<double, double, double, double>())
        .def_readwrite("bid_price", &OrderbookUnit::bid_price)
        .def_readwrite("bid_size", &OrderbookUnit::bid_size)
        .def_readwrite("ask_price", &OrderbookUnit::ask_price)
        .def_readwrite("ask_size", &OrderbookUnit::ask_size);

    py::class_<CoreTracker>(m, "CoreTracker")
        .def(py::init<>())
        .def("register_symbol", &CoreTracker::register_symbol)
        .def("add_tick", &CoreTracker::add_tick)
        .def("update_acc_24h", &CoreTracker::update_acc_24h)
        .def("get_metrics", &CoreTracker::get_metrics,
             py::arg("symbol"),
             py::arg("now"),
             py::arg("usdt_krw"),
             py::arg("ref_price"),
             py::arg("curr_spread"),
             py::arg("has_spread"),
             py::arg("kimchi_ema_alpha") = 0.002,
             py::arg("alpha") = 0.005)
        .def("get_profile", &CoreTracker::get_profile)
        .def("update_orderbook", &CoreTracker::update_orderbook)
        .def("add_history_tick", &CoreTracker::add_history_tick)
        .def("get_analysis_metrics", &CoreTracker::get_analysis_metrics,
             py::arg("symbol"),
             py::arg("level"),
             py::arg("current_time"),
             py::arg("imbalance_decay_rate"),
             py::arg("rsi_period"),
             py::arg("bucket_size"),
             py::arg("max_buckets"))
        .def("get_energy_map", &CoreTracker::get_energy_map,
             py::arg("symbol"),
             py::arg("window_sec"),
             py::arg("ref_now"));

    py::class_<RuleEvaluator>(m, "RuleEvaluator")
        .def(py::init<const py::dict&>())
        .def("should_buy", &RuleEvaluator::should_buy)
        .def("should_sell", &RuleEvaluator::should_sell)
        .def_property_readonly("symbol", &RuleEvaluator::get_symbol)
        .def_property_readonly("exchange", &RuleEvaluator::get_exchange)
        .def_property_readonly("budget", &RuleEvaluator::get_budget)
        .def_property_readonly("auto_trade", &RuleEvaluator::get_auto_trade)
        .def_property_readonly("active", &RuleEvaluator::get_active)
        .def_property_readonly("reference_market", &RuleEvaluator::get_reference_market)
        .def_property_readonly("orderbook_level", &RuleEvaluator::get_orderbook_level);

    py::class_<VirtualTrader>(m, "VirtualTrader")
        .def(py::init<double, double, bool>(),
             py::arg("initial_balance") = 10000000.0,
             py::arg("fee_rate") = 0.0005,
             py::arg("silent") = false)
        .def("buy", &VirtualTrader::buy,
             py::arg("symbol"), py::arg("price"), py::arg("amount"), py::arg("current_time"), py::arg("version"))
        .def("sell", &VirtualTrader::sell,
             py::arg("symbol"), py::arg("price"), py::arg("reason"), py::arg("current_time"), py::arg("version"))
        .def("get_stats", &VirtualTrader::get_stats, py::arg("current_prices"))
        .def_property("balance", &VirtualTrader::get_balance, &VirtualTrader::set_balance)
        .def_property("initial_balance", &VirtualTrader::get_initial_balance, &VirtualTrader::set_initial_balance)
        .def_property_readonly("positions", &VirtualTrader::get_positions)
        .def_property_readonly("history", &VirtualTrader::get_history)
        .def_property_readonly("symbol_stats", &VirtualTrader::get_symbol_stats)
        .def_property_readonly("total_trades", &VirtualTrader::get_total_trades)
        .def_property_readonly("win_trades", &VirtualTrader::get_win_trades)
        .def("clear_history", &VirtualTrader::clear_history)
        .def("clear_positions", &VirtualTrader::clear_positions)
        .def("remove_position", &VirtualTrader::remove_position, py::arg("symbol"))
        .def("reset", &VirtualTrader::reset, py::arg("new_initial_balance") = -1.0)
        .def("add_history_event", &VirtualTrader::add_history_event)
        .def("force_set_position", &VirtualTrader::force_set_position,
             py::arg("symbol"), py::arg("price"), py::arg("qty"), py::arg("entry_time"), py::arg("max_price"), py::arg("ts_active"));

    py::class_<Backtester>(m, "Backtester")
        .def(py::init<const py::dict&, double, double>(),
             py::arg("target_config"),
             py::arg("initial_balance") = 10000000.0,
             py::arg("fee_rate") = 0.0005)
        .def("load_ticks", &Backtester::load_ticks)
        .def("load_orderbooks", &Backtester::load_orderbooks)
        .def("load_ticks_from_csv", &Backtester::load_ticks_from_csv)
        .def("load_orderbooks_from_csv", &Backtester::load_orderbooks_from_csv)
        .def("run", &Backtester::run,
             py::arg("warmup_period") = 30.0,
             py::arg("reentry_cooldown") = 30.0);
}
