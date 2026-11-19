# 低估值维度（planb_value）gate 终审 —— 判决 REJECT（2026-11-19）

> 判据来源：`.workbuddy/memory/2026-11-17.md`「§四 五条判生死（预注册 fail-closed）」：
> ① DSR_def 过且 ΔDSR_def>0 ② PBO 不恶化（基线 47.62%）③ 超额 vs 同截面池等权为正
> ④ 市值中性化后增量仍显著 ⑤ T3 双窗同号。**任一不满足 = FAIL，gate FAIL 即否决、不回头调参。**
> 数据：runner `/opt/aqsp-runner/gate_run/`（`grid_profile=planb_value`，main `3972eb15` 含命门 B 全市场分位修复）；
> 判读：`scripts/research/value_gate_verdict.py`（§四 五条机械判读，fail-closed，绝不重算超额基准）。
> 原始产物：`/opt/aqsp-runner/gate_run/walkforward_gate.json`（DSR/PBO 绝对口径）
> + `/opt/aqsp-runner/gate_run/value_excess.json`（8 臂超额，`aqsp.backtest.excess` 唯一事实源）。

---

## 一、gate 绝对口径读数（`walkforward_gate.json`，planb_value 臂，9.8h 跑满 19/19）

| 字段 | 值 | 判定 |
|---|---:|---|
| deflated_sharpe (DSR) | **−2.4846** | **FAIL**（门槛 1.0） |
| PBO | 41.43% | PASS（< 基线 47.62% 且 < 0.5） |
| dsr_pass / pbo_pass | False / True | — |
| **both_pass** | **False** | **gate 绝对口径 = 不通过** |
| cscv_reliable / n_variants | True / 8 | CSCV 可靠（8 臂全活，`WB-VLOW-{10,20,30,50}x{3,5}`） |
| n_periods / skipped | 19 / 0 | 完整跑满 |
| effective_symbols | 5158（streaming，batch 500） | listing-aware 3y 窗 2023-11-17 ~ 2026-11-16 |

---

## 二、判据③ 超额 vs 池等权（`aqsp.backtest.excess` 唯一事实源，sample=1200 等距抽样）

全市场等权买入持有基准年化 **+12.73%**（bench 窗口 20180102 ~ 20261117）；
**8 臂 planb_value 策略年化全负**（−3.63% ~ −7.74%），超额年化中位 **−21.74%**。

| 臂 (top_n×horizon) | 策略年化 | 超额年化 | t | 跑赢期 |
|---|---:|---:|---:|---:|
| WB-VLOW-10x3 | −5.78% | −22.89% | −1.16 | 7/19 |
| WB-VLOW-10x5 | −7.74% | −24.30% | −1.32 | 8/19 |
| WB-VLOW-20x3 | −4.61% | −21.85% | −1.10 | 7/19 |
| WB-VLOW-20x5 | −5.57% | −22.46% | −1.19 | 9/19 |
| WB-VLOW-30x3 | −4.40% | −21.63% | −1.10 | 7/19 |
| WB-VLOW-30x5 | −4.38% | −21.50% | −1.11 | 9/19 |
| WB-VLOW-50x3 | −3.63% | −21.00% | −1.05 | 9/19 |
| WB-VLOW-50x5 | −3.86% | −21.09% | −1.07 | 9/19 |

- 8 臂超额**方向一致全负**、t 全在 −1.05 ~ −1.32（无一达显著），跑赢期 7~9/19 ⇒ **不是某臂噪声，是整条价值维度跑输等权**。
- 口径警示（`excess.py` docstring）：等权基准对样本量敏感（400/800/1200 ⇒ +6.5%/+6.5%/+1.9%）⇒ **只看方向与量级，不抠小数点**。本例 8 臂全负、量级一致，结论稳。

---

## 三、§四 五条机械判决（`value_gate_verdict.py` 输出，fail-closed）

| 判据 | 结果 | 依据 |
|---|---|---|
| ① DSR_def 过且 ΔDSR>0 | **FAIL** | 绝对 DSR −2.4846 ≤ 1.0，`dsr_pass=False` ⇒ 一票否决 |
| ② PBO 不恶化(≤47.62%) | PASS | PBO 41.43%，gate pbo_pass=True |
| ③ 超额 vs 池等权为正 | **FAIL** | 8 臂超额全负、中位 −21.74% |
| ④ 市值中性化后增量仍显著 | NOT_PRODUCED | 本轮 3y 单窗 planb_value gate 不含市值中性对照 |
| ⑤ T3 双窗同号 | NOT_PRODUCED | 本轮 3y 单窗 |
| **总体** | **REJECT（否决，不回头调参）** | ①③ 两条能产出的判据都 FAIL，一票否决 |

> **口径诚实**：④⑤ 本轮 3y 单窗不产出，但**不影响判决**——预注册 fail-closed 下，
> ①（DSR 绝对）与 ③（超额）两条能产出的判据**都已 FAIL**，任一 FAIL 即一票否决，
> 补 ④⑤ 也不会翻盘（它们只会进一步要求「市值中性后增量仍显著」+「双窗同号」，
> 而 ③ 已证明超额全负）。**REJECT 成立，不回头调 λ/权重/中性化、不重烧 9.8h gate。**

---

## 四、判决与后续

1. 🔴 **低估值维度（value / planb_value 臂）正式否决**：DSR 绝对口径 FAIL + 超额 8 臂全负，
   按预注册「gate FAIL 即否决、不回头调参」（红线「亏损就调参」禁止）。
2. **生产 `config/thresholds.yaml` `value.enabled` 维持 false 不动**（本轮 gate 本就用 `--skip-pit-financials`
   下 value 走预计算分位，生产打分行为零变化；否决 ⇒ 维持关闭，不开启）。
3. **不重开 gate、不重烧 9.8h**：判红权威（gate）已出结论，再跑只会复现同一否决。
4. **印证既有结论**（不新增、坐实）：
   - 11-13「换数据维度基本证伪」（486 检验：宽域 PIT 通过 7~10/35 vs 量价 22~27/37）；
   - 11-16 三条补因子路线全证伪（调权重无用）；
   - `pb_verdict.md`（行业内相对 PB）关 2 联合正交 / 关 3 λ 敏感性未通过；
   - ⇒ **独立正 alpha 稀缺**再次坐实，**alpha 赤字仍未填**。

---

## 五、可复现命令（判红权威事实源，不临场手算）

```bash
# gate verdict（runner）
scp aqsp-runner:/opt/aqsp-runner/gate_run/walkforward_gate.json /tmp/vgv/gate.json
# 8 臂超额（唯一事实源，runner venv）
/opt/aqsp-runner/venv/bin/python -c "
import sys, json; sys.path.insert(0,'/opt/aqsp-runner/aqsp-scheduler-current/src')
from aqsp.backtest.excess import load_arm_periods, equal_weight_curve, excess_for_arm
arms=load_arm_periods('/opt/aqsp-runner/gate_run').get('2023-11-17_2026-11-16',{})
curve=equal_weight_curve('/opt/aqsp-runner/data/astocks_raw.db',sample=1200)
out={}
for a,r in arms.items():
    s=excess_for_arm(r,curve)
    if s is None: continue   # 有效期 <3 跳过（fail-soft）
    t=s.excess_t
    out[a]={'excess_ann':round(s.excess_ann,6),'excess_t':(round(t,4) if t==t else None),
            'periods':s.periods,'beat_periods':s.beat_periods}
open('/opt/aqsp-runner/gate_run/value_excess.json','w').write(json.dumps({'arms':out}))"
# §四 五条机械判决单（本机，纯标准库）
python scripts/research/value_gate_verdict.py \
  --gate-json /tmp/vgv/gate.json \
  --excess /opt/aqsp-runner/gate_run/value_excess.json \
  --baseline-pbo 0.4762
```
