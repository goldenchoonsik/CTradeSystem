# -*- coding: utf-8 -*-
"""
CTradeSystem - Symbol Relations Manager (종목 상관관계 관리 엔진)
================================================================
1. shared/config/symbol_relations.json 선언적 리소스의 원자적 관리
2. 타겟 종목별 참조 대장주(선행 지표) 및 글로벌 매크로 관계 매핑
3. 종목 신원/검색은 종목 마스터 원장(config_manager)에 위임
"""

import os
import json
import tempfile
import logging
from typing import List, Dict, Any, Optional, Tuple

from shared.data_library.paths import data_vault_config_path
from shared.core.file_lock import file_locked, with_file_lock

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_LEGACY_RELATIONS_PATH = os.path.join(os.path.dirname(_MODULE_DIR), "config", "symbol_relations.json")
_VAULT_RELATIONS_PATH = data_vault_config_path("symbol_relations.json")

# DataVault/config/ 우선, 없으면 레거시 shared/config/ 폴백 (기본값: DataVault/config/)
_RELATIONS_FILE_PATH = _VAULT_RELATIONS_PATH if (os.path.exists(_VAULT_RELATIONS_PATH) or not os.path.exists(_LEGACY_RELATIONS_PATH)) else _LEGACY_RELATIONS_PATH
_LOCK = with_file_lock(_RELATIONS_FILE_PATH)  # read-modify-write 직렬화

# 공통 피처 그룹(Factor Basket) 저장소 경로 및 락
_VAULT_GROUPS_PATH = data_vault_config_path("symbol_groups.json")
_LEGACY_GROUPS_PATH = os.path.join(os.path.dirname(_MODULE_DIR), "config", "symbol_groups.json")
_GROUPS_FILE_PATH = _VAULT_GROUPS_PATH if (os.path.exists(_VAULT_GROUPS_PATH) or not os.path.exists(_LEGACY_GROUPS_PATH)) else _LEGACY_GROUPS_PATH
_GROUPS_LOCK = with_file_lock(_GROUPS_FILE_PATH)


class SymbolRelationsManager:
    """종목 간 상관관계(Reference/Macro) 및 종목 마스터 검색을 전담하는 매니저"""

    # 실제 학습 프로젝트(타겟)가 존재해야 정상인 시장 분류.
    # (GLOBAL_INDEX_CFD/COMMODITY_CFD/FX_SPOT 등은 다른 타겟의 참조 전용 매크로 프록시라
    #  프로젝트 없이 존재하는 게 정상이므로 고아 판정에서 제외)
    TRADEABLE_MARKET_TYPES = {"KRX_ETF", "KRX_STOCK", "CRYPTO", "CRYPTO_SPOT"}

    @classmethod
    def load_all_relations(cls) -> Dict[str, Any]:
        """전체 종목 상관관계 JSON 로드. 종목명은 항상 마스터 원장 기준으로 해석해서 반환한다
        (파일에 박제된 name 은 캐시일 뿐이며 원장과 다르면 원장이 이긴다)."""
        if not os.path.exists(_RELATIONS_FILE_PATH):
            return {}
        try:
            with open(_RELATIONS_FILE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logging.error(f"[SymbolRelationsManager] 관계 파일 로드 실패: {e}")
            return {}
        cls._hydrate_ledger_fields(data)
        return data

    # 타겟은 원장 소유 속성 전부(이름/시장/설명), 참조 항목은 종목 자체의 이름/설명만 원장에서 온다
    # (참조 항목의 role/default_price 는 관계 자체의 속성이라 여기 남는다).
    _TARGET_LEDGER_FIELDS = ("name", "market_type", "description")
    _REF_LEDGER_FIELDS = ("name", "description")
    # 폐지된 참조 필드: 참조 컬럼/피처 이름은 심볼에서 유도한다(config_manager.ref_key) — 별칭을 저장하지 않는다
    _RETIRED_REF_FIELDS = ("alias",)

    @classmethod
    def _hydrate_ledger_fields(cls, data: Dict[str, Any]) -> None:
        try:
            from shared.config.config_manager import get_ledger_fields
        except Exception:
            return
        for target_sym, info in data.items():
            if not isinstance(info, dict):
                continue
            info.update(get_ledger_fields(target_sym, cls._TARGET_LEDGER_FIELDS))
            for ref in info.get("references", []) or []:
                ref.update(get_ledger_fields(ref.get("symbol", ""), cls._REF_LEDGER_FIELDS))

    @classmethod
    def _strip_ledger_fields(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """저장용 사본에서 종목 마스터 원장이 값을 알고 있는 속성(이름/시장/설명)을 제거한다.
        원장에만 저장하고 로드 시 해석한다 (여러 파일에 복사 저장하면 드리프트한다).
        원장에 값이 없는 속성은 지우지 않으므로 어떤 경우에도 텍스트가 사라지지 않는다."""
        try:
            from shared.config.config_manager import get_ledger_fields
        except Exception:
            return data
        out: Dict[str, Any] = {}
        for target_sym, info in data.items():
            if not isinstance(info, dict):
                out[target_sym] = info
                continue
            entry = dict(info)
            for f in get_ledger_fields(target_sym, cls._TARGET_LEDGER_FIELDS):
                entry.pop(f, None)
            refs = []
            for ref in entry.get("references", []) or []:
                ref = dict(ref)
                for f in get_ledger_fields(ref.get("symbol", ""), cls._REF_LEDGER_FIELDS):
                    ref.pop(f, None)
                for f in cls._RETIRED_REF_FIELDS:      # 폐지된 필드는 어떤 호출자가 보내도 저장하지 않는다
                    ref.pop(f, None)
                refs.append(ref)
            if "references" in entry:
                entry["references"] = refs
            out[target_sym] = entry
        return out

    @classmethod
    def save_all_relations(cls, data: Dict[str, Any]) -> bool:
        """원자적(Atomic Write)으로 symbol_relations.json 저장 (이름/시장/설명은 원장에만 두므로 저장하지 않음)"""
        data = cls._strip_ledger_fields(data)
        dir_name = os.path.dirname(_RELATIONS_FILE_PATH)
        os.makedirs(dir_name, exist_ok=True)
        temp_name = None
        try:
            with file_locked(_RELATIONS_FILE_PATH):
                with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding="utf-8", suffix=".tmp") as tf:
                    temp_name = tf.name
                    json.dump(data, tf, indent=2, ensure_ascii=False)
                os.replace(temp_name, _RELATIONS_FILE_PATH)
            return True
        except Exception as e:
            logging.error(f"[SymbolRelationsManager] 관계 파일 저장 실패: {e}")
            if temp_name and os.path.exists(temp_name):
                try:
                    os.remove(temp_name)
                except OSError:
                    pass
            return False

    @classmethod
    def _resolve_target_key(cls, target_symbol: str, all_data: Dict[str, Any]) -> str:
        """all_data에서 target_symbol에 매칭되는 실제 키('069500', 'KRW-BTC', 'BTC' 등)를 유연하게 반환"""
        raw = str(target_symbol).strip().upper()
        clean = raw.replace("KRW-", "")
        if raw in all_data:
            return raw
        if clean in all_data:
            return clean
        if f"KRW-{clean}" in all_data:
            return f"KRW-{clean}"
        return clean

    # =========================================================================
    # 📦 피처 그룹 (Factor Basket) 관리 및 원장 수화 (Hydration) 엔진
    # =========================================================================

    @classmethod
    def load_all_groups(cls) -> Dict[str, Any]:
        """전체 피처 그룹(Factor Basket) JSON 로드. 멤버 종목 정보는 원장 기준으로 실시간 수화한다."""
        if not os.path.exists(_GROUPS_FILE_PATH):
            return {}
        try:
            with open(_GROUPS_FILE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logging.error(f"[SymbolRelationsManager] 피처 그룹 파일 로드 실패: {e}")
            return {}
        cls._hydrate_group_members(data)
        return data

    @classmethod
    def _hydrate_group_members(cls, groups: Dict[str, Any]) -> None:
        """그룹 내의 멤버 종목들에 마스터 원장(symbols_registry.json) 필드를 동적 주입"""
        try:
            from shared.config.config_manager import get_ledger_fields
        except Exception:
            return
        for gid, ginfo in groups.items():
            if not isinstance(ginfo, dict):
                continue
            def_role = ginfo.get("default_role", "leading_stock")
            hydrated_members = []
            for m in ginfo.get("members", []) or []:
                if isinstance(m, str):
                    m_dict = {"symbol": m, "role": def_role}
                elif isinstance(m, dict):
                    m_dict = dict(m)
                    if not m_dict.get("role"):
                        m_dict["role"] = def_role
                else:
                    continue
                sym = m_dict.get("symbol", "")
                m_dict.update(get_ledger_fields(sym, cls._REF_LEDGER_FIELDS))
                hydrated_members.append(m_dict)
            ginfo["members"] = hydrated_members

    @classmethod
    def _strip_group_fields(cls, groups: Dict[str, Any]) -> Dict[str, Any]:
        """저장 시 멤버 종목의 원장 필드(이름/설명 등)를 제거하여 Zero-Drift 보장"""
        out = {}
        for gid, ginfo in groups.items():
            if not isinstance(ginfo, dict):
                out[gid] = ginfo
                continue
            entry = dict(ginfo)
            def_role = entry.get("default_role", "leading_stock")
            stripped_members = []
            for m in entry.get("members", []) or []:
                if isinstance(m, str):
                    stripped_members.append({"symbol": m})
                elif isinstance(m, dict):
                    sym = m.get("symbol", "")
                    m_copy = {"symbol": sym}
                    if m.get("role") and m.get("role") != def_role:
                        m_copy["role"] = m["role"]
                    if m.get("default_price") is not None:
                        m_copy["default_price"] = m["default_price"]
                    stripped_members.append(m_copy)
            entry["members"] = stripped_members
            out[gid] = entry
        return out

    @classmethod
    def save_all_groups(cls, data: Dict[str, Any]) -> bool:
        """원자적(Atomic Write)으로 symbol_groups.json 저장"""
        data = cls._strip_group_fields(data)
        dir_name = os.path.dirname(_GROUPS_FILE_PATH)
        os.makedirs(dir_name, exist_ok=True)
        temp_name = None
        try:
            with file_locked(_GROUPS_FILE_PATH):
                with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding="utf-8", suffix=".tmp") as tf:
                    temp_name = tf.name
                    json.dump(data, tf, indent=2, ensure_ascii=False)
                os.replace(temp_name, _GROUPS_FILE_PATH)
            return True
        except Exception as e:
            logging.error(f"[SymbolRelationsManager] 피처 그룹 파일 저장 실패: {e}")
            if temp_name and os.path.exists(temp_name):
                try:
                    os.remove(temp_name)
                except OSError:
                    pass
            return False

    @classmethod
    def get_group(cls, group_id: str) -> Optional[Dict[str, Any]]:
        """특정 피처 그룹 상세 조회"""
        groups = cls.load_all_groups()
        return groups.get(str(group_id).strip().upper())

    @classmethod
    @_GROUPS_LOCK
    def create_or_update_group(cls, group_id: str, group_payload: Dict[str, Any]) -> bool:
        """신규 피처 그룹 생성 또는 기존 팩 메타데이터 갱신"""
        groups = cls.load_all_groups()
        gid = str(group_id or group_payload.get("id", "")).strip().upper()
        if not gid:
            return False
        
        # 멤버 종목 원장 존재 보장
        from shared.config.config_manager import ensure_symbol_registered
        members = []
        for m in group_payload.get("members", []) or []:
            sym = str(m.get("symbol") if isinstance(m, dict) else m).strip().upper()
            if sym:
                ensure_symbol_registered(sym)
                members.append(m if isinstance(m, dict) else {"symbol": sym})

        groups[gid] = {
            "id": gid,
            "name": str(group_payload.get("name", gid)).strip(),
            "category": str(group_payload.get("category", "SECTOR")).strip().upper(),
            "default_role": str(group_payload.get("default_role", "leading_stock")).strip(),
            "description": str(group_payload.get("description", "")).strip(),
            "members": members
        }
        return cls.save_all_groups(groups)

    @classmethod
    @_GROUPS_LOCK
    def delete_group(cls, group_id: str, force: bool = False) -> Tuple[bool, str]:
        """피처 그룹 삭제 (구독 중인 타겟이 있을 경우 force=False면 거부)"""
        groups = cls.load_all_groups()
        gid = str(group_id).strip().upper()
        if gid not in groups:
            return False, "존재하지 않는 팩입니다."

        rel_data = cls.load_all_relations()
        used_by = []
        for t_sym, t_info in rel_data.items():
            if gid in (t_info.get("groups") or []):
                used_by.append(t_sym)
        if used_by and not force:
            return False, f"현재 타겟({', '.join(used_by)})에서 사용 중인 팩입니다."

        if used_by and force:
            for t_sym in used_by:
                t_groups = rel_data[t_sym].get("groups", [])
                rel_data[t_sym]["groups"] = [g for g in t_groups if g != gid]
            cls.save_all_relations(rel_data)

        del groups[gid]
        success = cls.save_all_groups(groups)
        return success, "삭제 완료" if success else "저장 실패"

    @classmethod
    @_GROUPS_LOCK
    def add_member_to_group(cls, group_id: str, member_data: Any) -> bool:
        """팩에 신규 종목 추가"""
        groups = cls.load_all_groups()
        gid = str(group_id).strip().upper()
        if gid not in groups:
            return False
        
        from shared.config.config_manager import ensure_symbol_registered
        sym = str(member_data.get("symbol") if isinstance(member_data, dict) else member_data).strip().upper()
        if not sym:
            return False
        ensure_symbol_registered(sym)

        m_dict = dict(member_data) if isinstance(member_data, dict) else {"symbol": sym}
        members = groups[gid].get("members", [])
        for i, existing in enumerate(members):
            e_sym = str(existing.get("symbol") if isinstance(existing, dict) else existing).strip().upper()
            if e_sym == sym:
                members[i] = m_dict
                return cls.save_all_groups(groups)
        
        members.append(m_dict)
        groups[gid]["members"] = members
        return cls.save_all_groups(groups)

    @classmethod
    @_GROUPS_LOCK
    def remove_member_from_group(cls, group_id: str, symbol: str) -> bool:
        """팩에서 종목 제외"""
        groups = cls.load_all_groups()
        gid = str(group_id).strip().upper()
        if gid not in groups:
            return False
        clean_sym = str(symbol).strip().upper()
        members = groups[gid].get("members", [])
        new_members = [
            m for m in members
            if str(m.get("symbol") if isinstance(m, dict) else m).strip().upper() != clean_sym
        ]
        groups[gid]["members"] = new_members
        return cls.save_all_groups(groups)

    @classmethod
    @_LOCK
    def attach_group_to_target(cls, target_symbol: str, group_id: str) -> bool:
        """타겟 종목에 피처 그룹 바스켓 장착"""
        all_data = cls.load_all_relations()
        target_key = cls._resolve_target_key(target_symbol, all_data)
        gid = str(group_id).strip().upper()
        
        if gid not in cls.load_all_groups():
            return False

        if target_key not in all_data:
            from shared.config.config_manager import ensure_symbol_registered
            target_info = ensure_symbol_registered(target_key)
            all_data[target_key] = {
                "symbol": target_key,
                "name": target_info.get("name", target_key),
                "market_type": target_info.get("market_type", "KRX_ETF"),
                "groups": [],
                "references": []
            }

        groups = all_data[target_key].setdefault("groups", [])
        if gid not in groups:
            groups.append(gid)
        return cls.save_all_relations(all_data)

    @classmethod
    @_LOCK
    def detach_group_from_target(cls, target_symbol: str, group_id: str) -> bool:
        """타겟 종목에서 피처 그룹 바스켓 분리"""
        all_data = cls.load_all_relations()
        target_key = cls._resolve_target_key(target_symbol, all_data)
        gid = str(group_id).strip().upper()
        if target_key not in all_data:
            return False
        groups = all_data[target_key].get("groups", [])
        all_data[target_key]["groups"] = [g for g in groups if g != gid]
        return cls.save_all_relations(all_data)

    @classmethod
    def get_groups_for_target(cls, target_symbol: str) -> List[str]:
        """타겟 종목에 장착된 그룹 ID 목록 조회"""
        data = cls.load_all_relations()
        target_key = cls._resolve_target_key(target_symbol, data)
        entry = data.get(target_key)
        if entry and "groups" in entry:
            return list(entry["groups"])
        return []

    # =========================================================================
    # 🔗 참조 관계 조회 및 동적 평면화 (Flattening)
    # =========================================================================

    @classmethod
    def get_relations_for_target(cls, target_symbol: str) -> List[Dict[str, Any]]:
        """특정 타겟 종목에 연결된 대장주/매크로 목록 조회 (그룹 평면화 + 개별 참조 병합)"""
        data = cls.load_all_relations()
        target_key = cls._resolve_target_key(target_symbol, data)
        entry = data.get(target_key)
        if not entry:
            return []

        resolved_refs: List[Dict[str, Any]] = []
        seen_symbols = set()

        # 1. groups 에 등록된 팩들의 멤버 종목 전개
        groups_data = cls.load_all_groups()
        for gid in entry.get("groups", []) or []:
            grp = groups_data.get(gid)
            if not grp:
                continue
            def_role = grp.get("default_role", "leading_stock")
            for m in grp.get("members", []) or []:
                sym = str(m.get("symbol", "")).strip().upper() if isinstance(m, dict) else str(m).strip().upper()
                if not sym or sym in seen_symbols:
                    continue
                seen_symbols.add(sym)
                item = dict(m) if isinstance(m, dict) else {"symbol": sym}
                if not item.get("role"):
                    item["role"] = def_role
                item["group_id"] = gid
                item["group_name"] = grp.get("name", gid)
                resolved_refs.append(item)

        # 2. 개별 references 병합
        for ref in entry.get("references", []) or []:
            if not isinstance(ref, dict):
                continue
            sym = str(ref.get("symbol", "")).strip().upper()
            if not sym or sym in seen_symbols:
                continue
            seen_symbols.add(sym)
            resolved_refs.append(dict(ref))

        # 3. 마스터 원장 동적 수화 (이름, 설명 등)
        try:
            from shared.config.config_manager import get_ledger_fields
            for r in resolved_refs:
                r.update(get_ledger_fields(r.get("symbol", ""), cls._REF_LEDGER_FIELDS))
        except Exception:
            pass

        return resolved_refs

    # 공통 거래일 교집합으로 타겟 구간을 자르는(clamp) 참조의 role. 같은 거래소 캘린더를 공유하는 대장주만 해당한다.
    # 그 외(매크로 선물/CFD/환율 등)는 캘린더가 달라 교집합에 넣으면 타겟의 정상 거래일이 잘려 나가므로 자르지 않고 붙이기만 한다.
    CLAMPING_ROLES = ("leading_stock",)

    @classmethod
    def join_plan(cls, target_symbol: str) -> Dict[str, List[str]]:
        """타겟의 참조 결합 계획 — 모든 결합 경로(Gold 빌더/대시보드/구 빌더)가 공유하는 단일 분류.
        {"refs": 구간을 자르는 참조 심볼, "series": 자르지 않고 붙이기만 하는 참조 심볼} (원장 심볼, 순서 보존, 중복 제거).
        컬럼 이름은 분류와 무관하게 동일하다(config_manager.ref_prefix)."""
        refs: List[str] = []
        series: List[str] = []
        for r in cls.get_relations_for_target(target_symbol):
            sym = str(r.get("symbol", "")).strip().upper() if isinstance(r, dict) else ""
            if not sym or sym in refs or sym in series:
                continue
            (refs if str(r.get("role", "")).lower() in cls.CLAMPING_ROLES else series).append(sym)
        return {"refs": refs, "series": series}

    @classmethod
    def get_relations(cls, target_symbol: str) -> Optional[Dict[str, Any]]:
        """특정 타겟 종목의 전체 관계 엔트리 반환 (호환성 및 단일 엔트리 조회용)"""
        data = cls.load_all_relations()
        target_key = cls._resolve_target_key(target_symbol, data)
        return data.get(target_key)

    @classmethod
    @_LOCK
    def add_or_update_reference(cls, target_symbol: str, ref_data: Dict[str, Any]) -> bool:
        """특정 타겟 종목에 새로운 대장주/매크로 부착 및 저장"""
        all_data = cls.load_all_relations()
        target_key = cls._resolve_target_key(target_symbol, all_data)

        # 원장(symbols_registry.json)에 타겟/참조 심볼이 반드시 존재하도록 보장
        from shared.config.config_manager import ensure_symbol_registered
        target_info = ensure_symbol_registered(target_key)
        ref_sym_key = str(ref_data.get("symbol", "")).strip().upper()
        if ref_sym_key:
            ensure_symbol_registered(
                ref_sym_key,
                name=str(ref_data.get("name", "")),
                market_type=str(ref_data.get("market_type", "")) or "OTHER",
                description=str(ref_data.get("description", "")),
                tradable=False,
            )

        if target_key not in all_data:
            all_data[target_key] = {
                "symbol": target_key,
                "name": target_info.get("name", target_key),
                "market_type": target_info.get("market_type", "KRX_ETF"),
                "references": []
            }

        ref_sym = str(ref_data.get("symbol", "")).strip().upper()
        if not ref_sym:
            return False

        # 기존 항목 확인 및 업데이트
        refs = all_data[target_key].get("references", [])
        updated = False
        for i, existing in enumerate(refs):
            if existing.get("symbol") == ref_sym:
                refs[i] = {**existing, **ref_data}
                updated = True
                break

        if not updated:
            refs.append(ref_data)

        all_data[target_key]["references"] = refs
        return cls.save_all_relations(all_data)

    @classmethod
    @_LOCK
    def remove_reference(cls, target_symbol: str, ref_symbol: str) -> bool:
        """특정 타겟 종목에서 대장주/매크로 분리"""
        all_data = cls.load_all_relations()
        target_key = cls._resolve_target_key(target_symbol, all_data)
        clean_ref = str(ref_symbol).strip().upper()

        if target_key not in all_data:
            return False

        refs = all_data[target_key].get("references", [])
        new_refs = [r for r in refs if str(r.get("symbol")).strip().upper() != clean_ref]
        all_data[target_key]["references"] = new_refs
        return cls.save_all_relations(all_data)

    @classmethod
    def find_referencing_targets(cls, symbol: str) -> List[Dict[str, Any]]:
        """symbol이 SSOT(symbol_relations.json)에서 어떤 타겟의 참조종목으로 등재되어 있는지,
        또는 그 자신이 타겟으로 등재되어 있는지 조사 (Raw Store 삭제 전 안전성 검증용)"""
        data = cls.load_all_relations()
        clean = str(symbol).strip().upper().replace("KRW-", "")
        results = []
        for target_sym, info in data.items():
            t_clean = str(target_sym).strip().upper().replace("KRW-", "")
            if t_clean == clean:
                results.append({
                    "target_symbol": target_sym,
                    "target_name": info.get("name", target_sym),
                    "relation": "is_target",
                })
            for ref in info.get("references", []) or []:
                r_clean = str(ref.get("symbol", "")).strip().upper().replace("KRW-", "")
                if r_clean == clean:
                    results.append({
                        "target_symbol": target_sym,
                        "target_name": info.get("name", target_sym),
                        "relation": "referenced_as",
                        "role": ref.get("role", "reference"),
                    })
        return results

    @classmethod
    @_LOCK
    def remove_target(cls, target_symbol: str) -> bool:
        """타겟 종목 엔트리 자체(및 그에 달린 모든 참조 관계)를 SSOT에서 완전 제거"""
        all_data = cls.load_all_relations()
        target_key = cls._resolve_target_key(target_symbol, all_data)
        if target_key not in all_data:
            return False
        del all_data[target_key]
        return cls.save_all_relations(all_data)

    @classmethod
    def list_orphan_targets(cls, active_project_symbols: List[str]) -> List[Dict[str, Any]]:
        """
        symbol_relations.json(SSOT)에 실거래 가능 타겟(TRADEABLE_MARKET_TYPES)으로 등재되어 있지만
        현재 어떤 학습 프로젝트도 연결되어 있지 않은 "미사용 타겟 선언" 목록을 반환.
        (프로젝트가 삭제/유실된 뒤에도 선언이 남으면 그 종목과 참조종목이 수집 대장에 계속 자동 등록된다.
         타겟 선언이 없어도 다른 타겟의 참조종목으로는 계속 쓸 수 있으므로, 각 항목에 참조 사용처를 함께 반환한다)
        """
        active_clean = {str(s).strip().upper().replace("KRW-", "") for s in active_project_symbols}
        data = cls.load_all_relations()
        orphans = []
        for target_sym, info in data.items():
            market_type = info.get("market_type", "")
            if market_type not in cls.TRADEABLE_MARKET_TYPES:
                continue
            t_clean = str(target_sym).strip().upper().replace("KRW-", "")
            if t_clean in active_clean:
                continue
            refs = info.get("references", []) or []
            used_by = [
                {"target_symbol": h["target_symbol"], "target_name": h["target_name"], "role": h.get("role", "")}
                for h in cls.find_referencing_targets(target_sym)
                if h["relation"] == "referenced_as"
            ]
            orphans.append({
                "target_symbol": target_sym,
                "target_name": info.get("name", target_sym),
                "market_type": market_type,
                "used_as_reference_by": used_by,
                "reference_count": len(refs),
                "references": [
                    {"symbol": r.get("symbol"), "name": r.get("name"), "role": r.get("role")}
                    for r in refs
                ],
            })
        return orphans

    @classmethod
    def find_symbol_info(cls, symbol_or_code: str) -> Dict[str, Any]:
        """단일 종목의 메타데이터 조회 (종목 마스터 원장 경유, 'KRW-' 표기 차이 허용)"""
        from shared.config.config_manager import get_symbol_info, get_symbol_name
        raw = str(symbol_or_code).strip().upper()
        clean = raw.replace("KRW-", "")
        for candidate in (raw, clean, f"KRW-{clean}"):
            if get_symbol_name(candidate):
                return get_symbol_info(candidate)
        return get_symbol_info(raw)

    @classmethod
    def get_available_vault_references(cls, target_symbol: str) -> Dict[str, Any]:
        """DataVault에 실제 수집된 자산 중 현재 타겟에 미등록된 가용 참조 자산 및 전체 수집 자산 조회.

        타겟 자산군과의 친화도(동일 시장 > 매크로 CFD/선물 > 타 시장)에 따라 우선순위 정렬.
        """
        from shared.data_library.source_mapping_manager import SourceMappingManager
        from shared.data_library import paths as dpaths
        from shared.config.config_manager import get_symbol_info

        clean_target = SourceMappingManager.clean_symbol(target_symbol)
        target_info = get_symbol_info(clean_target)
        target_mtype = str(target_info.get("market_type") or "").upper()
        is_crypto_target = (
            "CRYPTO" in target_mtype
            or "CRYPTO" in clean_target.upper()
            or clean_target.startswith("KRW-")
            or "BTC" in clean_target.upper()
            or "ETH" in clean_target.upper()
        )

        # 현재 타겟에 이미 등록된 참조/시리즈 종목 집합
        attached_symbols = {r["symbol"] for r in cls.get_relations_for_target(clean_target)}

        mappings = SourceMappingManager.load_all_mappings()
        candidate_scales = ("10s", "1m", "5m", "15m", "60m", "1d")

        all_collected = []
        unattached_collected = []

        for sym, meta in mappings.items():
            clean_sym = SourceMappingManager.clean_symbol(sym)
            if clean_sym == clean_target:
                continue

            # 수집된 정규화 스케일 조사
            found_scales = []
            for sc in candidate_scales:
                p = dpaths.normalized_path(f"candles_{sc}", f"{clean_sym}_{sc}.parquet")
                pm = dpaths.macro_normalized_path(clean_sym, sc)
                if (p and os.path.exists(p)) or (pm and os.path.exists(pm)):
                    found_scales.append(sc)

            if not found_scales:
                continue

            info = get_symbol_info(clean_sym)
            mtype = str(info.get("market_type") or meta.get("market_type") or meta.get("category") or "").upper()
            provider = meta.get("primary_source") or info.get("exchange", "")
            is_attached = clean_sym in attached_symbols

            # 추천 역할 (default_role)
            if "PERP" in clean_sym or "FUTURES" in mtype:
                def_role = "crypto_futures" if "CRYPTO" in mtype or "PERP" in clean_sym else "macro_futures"
            elif "CFD" in mtype or "MACRO" in mtype or provider == "CTRADER":
                def_role = "macro_futures"
            elif "CRYPTO" in mtype or clean_sym.startswith("KRW-"):
                def_role = "crypto_spot"
            else:
                def_role = "leading_stock"

            def_price = info.get("default_price") or meta.get("default_price") or 100000.0
            if "US_TECH" in clean_sym:
                def_price = 24000.0
            elif "US_500" in clean_sym:
                def_price = 5600.0
            elif "XAUUSD" in clean_sym:
                def_price = 2600.0

            item = {
                "symbol": clean_sym,
                "name": info.get("name") or meta.get("name") or clean_sym,
                "market_type": mtype,
                "provider": provider,
                "collected_scales": found_scales,
                "is_attached": is_attached,
                "default_role": def_role,
                "default_price": def_price,
                "description": info.get("description") or meta.get("description", ""),
            }
            all_collected.append(item)
            if not is_attached:
                unattached_collected.append(item)

        # 우선순위 정렬 함수
        def _sort_key(item):
            m = item["market_type"]
            p = item["provider"]
            s = item["symbol"]
            if is_crypto_target:
                # 1순위: 크립토 (선물/현물/테더)
                if "CRYPTO" in m or p in ("BINANCE", "UPBIT", "COINBASE") or "USDT" in s or "BTC" in s:
                    return (0, 0 if "PERP" in s else (1 if "USDT" in s else 2))
                # 2순위: 글로벌 매크로 CFD / FX
                if "CFD" in m or "MACRO" in m or p == "CTRADER" or "US_" in s:
                    return (1, 0)
                # 3순위: 국내 주식
                return (2, 0)
            else:
                # KRX 타겟:
                # 1순위: 국내 주식/ETF/선물
                if "KRX" in m or p in ("CREON", "KIS"):
                    return (0, 0)
                # 2순위: 글로벌 매크로 CFD
                if "CFD" in m or "MACRO" in m or p == "CTRADER":
                    return (1, 0)
                # 3순위: 크립토
                return (2, 0)

        unattached_collected.sort(key=_sort_key)
        all_collected.sort(key=_sort_key)

        return {
            "target": clean_target,
            "unattached": unattached_collected,
            "all_collected": all_collected,
            "counts": {
                "unattached": len(unattached_collected),
                "total_collected": len(all_collected),
            }
        }
