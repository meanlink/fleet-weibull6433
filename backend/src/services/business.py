"""Two-parameter Weibull maximum likelihood with right-censored observations."""
import math
import random
import numpy as np
from scipy.optimize import minimize


def metadata():
    return {
        "name": "车队部件寿命估计", "short": "fleet-weibull", "tagline": "保留尚未失效部件，用删失似然估计预防检修窗口",
        "client": "模拟客户：鹭湾公交机务组，96 辆公交车跟踪两种空气压缩机。",
        "workflow": "机务组每月导出更换与在役记录，只对已坏件求平均寿命，再用统一里程安排检修。",
        "pains": ["只看坏件会丢掉在役件的寿命信息", "不同压缩机型号被混在一个均值内", "检修里程未表达可靠度假设", "跨月补报重复资产无法直接合并，需要按截止月归并样本"],
        "upstream": "部件安装里程、每月补报里程与失效状态、截止日仍在役里程", "downstream": "机务检修评审，人工结合安全规程安排检查。",
        "entities": ["部件寿命样本", "可靠度估计", "机务简报计划"], "dataset_name": "鹭湾公交_压缩机月度补报_截止2026-11.csv",
        "columns": [{"key": "asset", "label": "部件资产号", "type": "string"}, {"key": "model", "label": "型号", "type": "string"}, {"key": "km", "label": "观测运行里程", "type": "number"}, {"key": "failed", "label": "已失效1/在役0", "type": "number"}, {"key": "observed_month", "label": "观测月(YYYY-MM)", "type": "string", "optional": True}],
        "parameters": [{"key": "model", "label": "部件型号", "type": "select", "default": "AC-8", "options": [{"value": "AC-8", "label": "AC-8 螺杆机"}, {"value": "AC-12", "label": "AC-12 改进型"}]}, {"key": "reliability", "label": "目标可靠度", "type": "number", "default": 0.9, "min": 0.6, "max": 0.99}, {"key": "cutoff_month", "label": "评估截止月", "type": "month", "default": "2026-11", "pattern": "^\\d{4}-\\d{2}$"}],
        "workbench_title": "删失样本与检修可靠度", "action_label": "拟合寿命曲线", "unit": "公里", "accent": "oklch(51% 0.13 35)", "style": "fleet-chart",
        "seed_story": ["08:00 导入鹭湾公交_压缩机月度补报_截止2026-11.csv，共200件部件的跨月补报记录。", "08:20 对照资产编号确认 failed=0 是截止仍在役，不是零寿命。", "09:00 选择 AC-8，评估截止月设为 2026-11。", "09:15 晚到记录按观测月归位，截止月后的补录不参与，执行含右删失的 Weibull 极大似然估计。", "09:30 查看形状参数、B10寿命和目标可靠度里程，并逐件复核取舍依据。", "10:00 切到 AC-12 独立拟合，避免混合型号。", "14:00 将曲线与样本结构提交机务评审，并创建本地分发记录。"],
        "method": "按评估截止月归并同资产跨月观测（取截止月内最新一条），独立右删失条件下 L=∏f(t)^δS(t)^(1−δ)，f为二参数Weibull密度，S(t)=exp[−(t/η)^β]。在logβ/logη空间求极大似然；目标里程t=η[−ln R]^(1/β)。",
        "limitations": ["假设独立删失和同质运行工况，未纳入线路、载荷、批次等协变量。", "里程阈值是样本模型估计，不替代厂家安全规范，也未包含参数置信区间。", "失效后又报在役或里程倒退的数据冲突会阻止估计，须由机务组核对原始台账后修正。"],
    }


def seed_records():
    """兼容旧入口：等价于截止月样本的一行/件版本。"""
    snapshot = seed_monthly_records()
    return [
        {"asset": row["asset"], "model": row["model"], "km": row["km"],
         "failed": row["failed"], "observed_month": row["observed_month"]}
        for row in snapshot["flat"]
        if row["observed_month"] == snapshot["cutoff"]
    ]


def seed_monthly_records():
    """生成 2026-06 至 2026-11 的月度补报台账。

    包含真实业务特征：同一资产多月重复上报、晚到补报（批次来源月晚于观测月）、
    截止月之后的补录（2026-12）。失效复活与里程倒退的冲突样例单独构造，
    由 seed_conflict_records 组成独立的核对演示批次，主基线批次本身无冲突。
    """
    rng = random.Random(1017)
    months = ["2026-06", "2026-07", "2026-08", "2026-09", "2026-10", "2026-11"]
    cutoff = "2026-11"
    flat, latest = [], {}
    for model, scale, shape in (("AC-8", 88000, 2.4), ("AC-12", 116000, 1.8)):
        for index in range(100):
            asset = f"{model}-{index + 1:03}"
            failure = rng.weibullvariate(scale, shape)
            failed_already = False
            km = round(rng.uniform(12000, 30000), 1)
            for month in months:
                # 每月增量独立采样，但里程只增不减，符合真实台账的单调累计口径。
                km = round(km + rng.uniform(6000, 11000), 1)
                if not failed_already and failure <= km:
                    km = round(failure, 1)
                    failed = 1
                    failed_already = True
                else:
                    failed = 1 if failed_already else 0
                flat.append({"asset": asset, "model": model, "km": km,
                             "failed": failed, "observed_month": month})
                if month <= cutoff:
                    latest[asset] = {"asset": asset, "model": model,
                                     "km": km, "failed": failed,
                                     "observed_month": month}
    # 晚到补报：2026-11 批次里补录两件部件 2026-09 的真实状态，里程低于 11 月现值，按观测月归位后被更新观测替代。
    late_assets = ["AC-8-007", "AC-12-009"]
    for asset in late_assets:
        current = latest[asset]
        sept = next(r for r in flat if r["asset"] == asset and r["observed_month"] == "2026-09")
        row = {"asset": asset, "model": current["model"],
               "km": round(sept["km"] - 2000.0, 1),
               # 09 月台账若已失效，晚到补报保持失效；否则为在役。
               "failed": sept["failed"], "observed_month": "2026-09"}
        flat.append(row)
    # 截止月之后的补录，重算沿用原截止月时不应参与。
    for asset in ("AC-8-003", "AC-12-005"):
        row = dict(latest[asset])
        row["observed_month"] = "2026-12"
        row["km"] = round(row["km"] + 9000, 1)
        flat.append(row)
    # 两件状态/里程冲突（放在独立演示批次中使用，主基线批次剔除）。
    conflicts = [
        {"asset": "AC-8-CONF-1", "model": "AC-8", "km": 72000.0, "failed": 1, "observed_month": "2026-09"},
        {"asset": "AC-8-CONF-1", "model": "AC-8", "km": 76000.0, "failed": 0, "observed_month": "2026-10"},
        {"asset": "AC-12-CONF-1", "model": "AC-12", "km": 90000.0, "failed": 0, "observed_month": "2026-08"},
        {"asset": "AC-12-CONF-1", "model": "AC-12", "km": 81000.0, "failed": 0, "observed_month": "2026-09"},
    ]
    return {"flat": flat, "cutoff": cutoff, "latest": list(latest.values()), "conflicts": conflicts}


def seed_conflict_records():
    """冲突演示批次：每型号 30 件，其中各含 1 件业务冲突，估计必须被阻止。"""
    rng = random.Random(2026)
    months = ["2026-09", "2026-10", "2026-11"]
    rows = []
    for model, scale, shape in (("AC-8", 88000, 2.4), ("AC-12", 116000, 1.8)):
        for index in range(30):
            asset = f"{model}-X{index + 1:02}"
            failure = rng.weibullvariate(scale, shape)
            km = round(rng.uniform(20000, 40000), 1)
            failed_already = False
            for month in months:
                km = round(km + rng.uniform(7000, 12000), 1)
                if not failed_already and failure <= km:
                    km = round(failure, 1)
                    failed = 1
                    failed_already = True
                else:
                    failed = 1 if failed_already else 0
                rows.append({"asset": asset, "model": model, "km": km,
                             "failed": failed, "observed_month": month})
    rows.extend(seed_monthly_records()["conflicts"])
    return rows


def analyze(records, params):
    """旧接口：直接对已归并的一行/件记录做 Weibull 拟合。"""
    from src.services.samples import build_sample
    cutoff = str(params.get("cutoff_month", "2026-11"))
    adjusted = dict(params)
    adjusted["cutoff_month"] = cutoff
    snapshot = build_sample(records, adjusted)
    return fit_weibull(snapshot)


def fit_weibull(snapshot):
    """对 build_sample 产出的样本快照执行二参数 Weibull 极大似然估计。"""
    reliability = snapshot.get("reliability")
    if reliability is None:
        reliability = 0.9
    try:
        reliability = float(reliability)
    except (ValueError, TypeError):
        raise ValueError("目标可靠度必须是数值") from None
    if not math.isfinite(reliability) or not 0.6 <= reliability <= 0.99:
        raise ValueError("目标可靠度须介于0.6和0.99")

    if snapshot["conflicts"]:
        labels = "；".join(dict.fromkeys(c["label"] for c in snapshot["conflicts"]))
        raise ValueError(f"样本存在 {len(snapshot['conflicts'])} 条业务冲突（{labels}），已阻止估计，请先核对台账")
    if snapshot["malformed"]:
        raise ValueError(f"存在 {len(snapshot['malformed'])} 行无法识别的记录，已阻止估计")

    times = [row["km"] for row in snapshot["sample"]]
    events = [row["failed"] for row in snapshot["sample"]]
    model_name = snapshot["model"]
    for time in times:
        if not math.isfinite(time) or not 1 <= time <= 10_000_000:
            raise ValueError("里程须为1至1000万公里的有限数值")
    failures = sum(events)
    if len(times) < 25 or failures < 10:
        raise ValueError(f"所选型号在截止月至少需要25件样本和10件已失效样本（当前 {len(times)} 件、{failures} 件失效）")
    t = np.asarray(times, dtype=float)
    event = np.asarray(events, dtype=float)
    log_t = np.log(t)

    def objective(theta):
        shape, scale = np.exp(theta)
        powers = np.exp(np.clip(shape * (log_t - math.log(scale)), -600, 600))
        log_density = math.log(shape) - shape * math.log(scale) + (shape - 1) * log_t
        return float(np.sum(powers - event * log_density))

    solution = minimize(objective, np.log([1.8, float(np.median(t)) * 1.3]), method="L-BFGS-B", bounds=[(math.log(0.1), math.log(15)), (math.log(float(t.min()) * 0.1), math.log(float(t.max()) * 100))])
    if not solution.success or not math.isfinite(float(solution.fun)):
        raise ValueError("寿命似然未收敛，请检查样本或增加失效观测")
    shape, scale = [float(value) for value in np.exp(solution.x)]
    b10 = scale * (-math.log(0.9)) ** (1 / shape)
    threshold = scale * (-math.log(reliability)) ** (1 / shape)
    mean_life = scale * math.gamma(1 + 1 / shape)
    levels = sorted({0.99, 0.95, 0.9, 0.8, 0.7, reliability}, reverse=True)
    rows = [{"reliability": round(level, 4), "km": round(scale * (-math.log(level)) ** (1 / shape), 1), "failed_pct": round((1-level)*100, 2), "target": "本次目标" if level == reliability else "参考水平"} for level in levels]
    chart_x = np.linspace(0, float(t.max()) * 1.2, 41)
    curve = [round(math.exp(-(float(value) / scale) ** shape) * 100, 4) for value in chart_x]
    censored = len(times) - failures
    warnings = []
    if censored / len(times) > 0.6:
        warnings.append("超过60%的样本仍在役，尾部寿命外推不稳定。")
    if threshold > max(times):
        warnings.append("目标可靠度里程超过最大观测里程，属于外推。")
    if shape < 0.11 or shape > 14.9:
        warnings.append("形状参数接近优化边界，当前二参数假设可能不适合数据。")
    stats = snapshot["stats"]
    return {
        "summary": f"{model_name} 截止 {snapshot['cutoff_month']} 的{len(times)}件样本含{failures}件失效、{censored}件右删失；目标可靠度{reliability:.0%}对应运行里程约{threshold:,.0f}公里。",
        "metrics": [{"label": "形状参数β", "value": round(shape, 4)}, {"label": "尺度参数η", "value": round(scale, 1), "unit": "公里"}, {"label": "B10寿命", "value": round(b10, 1), "unit": "公里"}, {"label": "目标可靠度里程", "value": round(threshold, 1), "unit": "公里"}],
        "table": {"columns": [{"key": "reliability", "label": "可靠度R"}, {"key": "km", "label": "运行里程"}, {"key": "failed_pct", "label": "累计失效概率%"}, {"key": "target", "label": "用途"}], "rows": rows},
        "chart": {"type": "line", "labels": [str(round(float(value))) for value in chart_x], "series": [{"name": "仍正常运行概率%", "values": curve}]},
        "findings": [f"β={shape:.3f}，{'提示随里程增加的磨损趋势' if shape > 1 else '提示早期失效或近似恒定风险，应检查样本同质性'}；这不是因果判断。", f"模型平均寿命为{mean_life:,.0f}公里；均值不等于预防检修推荐里程。", f"在役件贡献生存概率，不被错误当成已失效件；删失比例{censored/len(times):.1%}。", f"样本按评估截止月 {snapshot['cutoff_month']} 归并：{stats['assets']} 个资产纳入 {stats['included']} 件，晚到归位 {stats['late_arrivals']} 件，截止月后剔除 {stats['after_cutoff']} 条观测。"],
        "method": metadata()["method"], "warnings": warnings,
        "cutoff_month": snapshot["cutoff_month"],
        "sample_stats": {"sample_size": len(times), "failures": failures, "censored": censored, **stats},
        "conflicts": snapshot["conflicts"],
    }
