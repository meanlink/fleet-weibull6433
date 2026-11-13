"""按月归并的部件寿命样本构建。

机务组每月对同一部件补报里程与失效状态，记录可能晚到（入库批次晚于观测月），
也可能包含截止月之后的补录。本模块以“评估截止月”为口径：

1. 晚到记录按观测月（observed_month）归位，而不是按入库时间；
2. 观测月晚于截止月的记录不参与本次估计；
3. 同一资产取截止月内最新一条观测作为寿命样本；
4. 已失效后又报在役、或里程随观测月倒退，列为冲突并阻止估计；
5. 每个资产的全部观测与取舍理由随任务快照留存，工作台可逐件复核。
"""
import re

MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
FALLBACK_MONTH = "2026-11"


def parse_month(value):
    """返回 YYYY-MM 形式的观测月；无法识别时抛出 ValueError。"""
    if value is None:
        raise ValueError("缺少观测月 observed_month（应为 YYYY-MM）")
    text = str(value).strip()
    match = MONTH_RE.match(text)
    if not match:
        raise ValueError(f"观测月格式非法：{text}，应为 YYYY-MM")
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        raise ValueError(f"观测月月份越界：{text}")
    return f"{year:04d}-{month:02d}"


def source_month(source):
    """从批次来源名中推断观测月，兼容 202611 与 2026-11 两种写法。"""
    match = re.search(r"(20\d{2})[-_]?(\d{2})", str(source or ""))
    if match and 1 <= int(match.group(2)) <= 12:
        return f"{match.group(1)}-{match.group(2)}"
    return FALLBACK_MONTH


def _row_month(row, source):
    raw = row.get("observed_month")
    if raw is None or str(raw).strip() == "":
        return source_month(source), False
    return parse_month(raw), True


def _conflict_decisions(asset, model, ordered):
    """识别同一资产观测序列内的业务冲突。"""
    conflicts = []
    failed_seen_month = None
    for month, km, failed in ordered:
        if failed == 1 and failed_seen_month is None:
            failed_seen_month = month
        if failed_seen_month is not None and month > failed_seen_month and failed == 0:
            conflicts.append({
                "type": "revival",
                "label": "失效后又报在役",
                "asset": asset,
                "model": model,
                "months": [failed_seen_month, month],
                "detail": f"{asset} 在 {failed_seen_month} 已报失效，{month} 又报在役，状态矛盾",
            })
    for previous, current in zip(ordered, ordered[1:]):
        if current[1] < previous[1]:
            conflicts.append({
                "type": "mileage_regression",
                "label": "里程倒退",
                "asset": asset,
                "model": model,
                "months": [previous[0], current[0]],
                "detail": f"{asset} 里程由 {previous[0]} 的 {previous[1]} 公里倒退为 {current[0]} 的 {current[1]} 公里",
            })
    return conflicts


def build_sample(records, params, source="manual.json"):
    """按评估截止月整理寿命样本，返回含样本、冲突与逐件取舍依据的结构。

    不会抛出业务口径错误（非法月份除外）；冲突与样本量不足通过返回值表达，
    由 fit_weibull / 任务执行层决定是否阻止估计。
    """
    cutoff = parse_month(params.get("cutoff_month"))
    model_filter = str(params.get("model", "AC-8"))

    grouped = {}
    malformed = []
    for index, row in enumerate(records):
        try:
            asset = str(row["asset"]).strip()
            model = str(row["model"]).strip()
            km = float(row["km"])
            failed_raw = float(row["failed"])
            month, explicit = _row_month(row, source)
        except (KeyError, TypeError, ValueError) as exc:
            malformed.append({"row": index + 1, "reason": str(exc)})
            continue
        if not asset:
            malformed.append({"row": index + 1, "reason": "资产号为空"})
            continue
        if failed_raw not in (0.0, 1.0):
            malformed.append({"row": index + 1, "reason": f"资产 {asset} 的失效标记 {row.get('failed')} 非法，只允许 0 或 1"})
            continue
        grouped.setdefault(asset, {"model": model, "models": {model}, "obs": []})
        grouped[asset]["models"].add(model)
        grouped[asset]["obs"].append(
            {"month": month, "km": km, "failed": int(failed_raw),
             "explicit_month": explicit, "row": index + 1}
        )

    conflicts, decisions, sample = [], [], []
    stats = {"assets": 0, "included": 0, "failed": 0, "censored": 0,
             "late_arrivals": 0, "after_cutoff": 0, "conflict_assets": 0,
             "malformed_rows": len(malformed)}

    for asset in sorted(grouped):
        info = grouped[asset]
        ordered = sorted((o["month"], o["km"], o["failed"]) for o in info["obs"])
        asset_conflicts = _conflict_decisions(asset, info["model"], ordered)
        if len(info["models"]) > 1:
            asset_conflicts.append({
                "type": "model_mismatch",
                "label": "型号不一致",
                "asset": asset,
                "model": info["model"],
                "months": [item[0] for item in ordered],
                "detail": f"{asset} 的型号在各月观测中不一致：{'、'.join(sorted(info['models']))}",
            })
        conflicts.extend(asset_conflicts)
        eligible = [o for o in info["obs"] if o["month"] <= cutoff]
        eligible.sort(key=lambda o: (o["month"], o["row"]))
        after = [o for o in info["obs"] if o["month"] > cutoff]
        stats["after_cutoff"] += len(after)
        chosen = eligible[-1] if eligible and not asset_conflicts else None

        observation_rows = []
        for o in sorted(info["obs"], key=lambda x: (x["month"], x["row"])):
            if o["month"] > cutoff:
                inclusion, reason = "excluded", f"观测月 {o['month']} 晚于截止月 {cutoff}，不参与本次估计"
            elif asset_conflicts:
                inclusion, reason = "conflict", "资产存在冲突记录，整组阻止参与估计"
            elif chosen is not None and o["row"] == chosen["row"]:
                inclusion = "included"
                reason = f"截止月内最新观测（{o['month']}），代表该部件当前寿命状态"
            else:
                inclusion, reason = "superseded", f"已被 {chosen['month'] if chosen else cutoff} 的更新观测替代"
            if inclusion == "included" and not o["explicit_month"]:
                stats["late_arrivals"] += 1
                reason += "；该记录未标注观测月，按批次来源月归位（晚到补报）"
            observation_rows.append({
                "month": o["month"], "km": o["km"], "failed": o["failed"],
                "explicit_month": o["explicit_month"], "row": o["row"],
                "inclusion": inclusion, "reason": reason,
            })

        if asset_conflicts:
            stats["conflict_assets"] += 1
            decision = "conflict"
            selected = None
        elif not eligible:
            decision = "excluded"
            selected = None
        else:
            decision = "included"
            selected = {"asset": asset, "model": info["model"], "km": chosen["km"],
                        "failed": chosen["failed"], "month": chosen["month"]}
            sample.append(selected)
            stats["included"] += 1
            if chosen["failed"]:
                stats["failed"] += 1
            else:
                stats["censored"] += 1

        stats["assets"] += 1
        decisions.append({
            "asset": asset, "model": info["model"], "decision": decision,
            "selected": selected,
            "conflicts": [c["detail"] for c in asset_conflicts],
            "observations": observation_rows,
        })

    model_sample = [row for row in sample if row["model"] == model_filter]
    return {
        "cutoff_month": cutoff,
        "model": model_filter,
        "stats": stats,
        "conflicts": conflicts,
        "malformed": malformed,
        "decisions": decisions,
        "sample": model_sample,
        "sample_size": len(model_sample),
        "failures": sum(1 for row in model_sample if row["failed"] == 1),
    }


def run_analysis(records, params, source="manual.json"):
    """样本构建 + Weibull 拟合的完整入口，供 worker 与 seed 使用。"""
    from src.services.business import fit_weibull
    snapshot = build_sample(records, params, source)
    snapshot["reliability"] = params.get("reliability", 0.9)
    fit = fit_weibull(snapshot)
    return {**fit, "sample_snapshot": snapshot}
