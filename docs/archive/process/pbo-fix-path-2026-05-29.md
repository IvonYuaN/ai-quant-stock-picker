# PBO 修复路径选型 — 2026-05-29

## TL;DR

**推荐路径: A** — 真 CSCV + 参数网格。唯一路径能在不改宪法的前提下给出严格的过拟合概率估计。

---

## 问题定义

当前 `_calculate_pbo` 不是 CSCV（详见 [pbo-audit-2026-05-29.md](pbo-audit-2026-05-29.md)）。核心矛盾：CSCV 要求 N 个策略配置构成 T×N 矩阵，但我们是单策略 walkforward，N=1。

§1.3 #12 双重门当前状态：DSR 侧 7/9 > 1.0（PASS），PBO 侧 9/9 = 75% > 50%（FAIL）。PBO 是唯一卡点。如果 PBO 实现有误，修复可能解锁门；如果修复后 PBO 仍然 > 50%，说明策略确实过拟合。

---

## 四条路径评估

### 路径 A：真 CSCV + 参数网格

**描述**：在当前复合策略的权重空间上生成 N 个变体（如 momentum_weight × triple_rise_weight 的网格），每个变体跑一次 walkforward，构建 T×N 矩阵，再按 Bailey, López de Prado & Zhu (2014) §3.1 Algorithm 1 执行标准 CSCV。

**可行性**：
- 实现复杂度：**天级**（~2-4 小时开发 + 测试）
- 参数网格：当前有 2 个活跃权重（momentum, triple_rise），步长 0.1 → 约 10-15 个变体；若加入 min_total_score 维度 → 约 30-50 个变体
- 每个变体跑一次 walkforward（sqlite_db 源，~1 分钟/变体）→ 总耗时 ~1 小时
- CSCV 组合数：S=10 时 C(10,5)=252，计算量可忽略
- **不需要改宪法** §1.3 #12（PBO 门的定义不变，只是实现从伪 CSCV 改成真 CSCV）

**预估能否解锁双门**：如果策略在不同权重配置下的 test-period 排名与 train-period 排名正相关（即最优权重在样本内外都表现好），PBO 会降到 < 50%，双门解锁。如果排名负相关（最优权重在样本外反而差），PBO 仍然高，说明真过拟合。

**主要风险**：
- 权重网格的粒度影响 N 的大小——太粗可能漏掉过拟合区域，太细计算量爆炸
- walkforward 本身的 stock selection 随权重变化，T×N 矩阵的列之间不是纯参数差异，还混入了选股差异
- 需要决定 S 的取值（论文建议 S 为偶数，常用 S=10 或 S=16）

**论文依据**：Bailey, López de Prado & Zhu (2014). "The Probability of Backtest Overfitting." SSRN preprint；后正式刊于 Journal of Computational Finance, 20(4), 39-69 (2017)。本文档统一引用 2014 年初版年份，§3.1 Algorithm 1 步骤 1-5 在两版中一致。

---

### 路径 B：White's Reality Check / Hansen SPA

**描述**：White (2000) Reality Check 和 Hansen (2005) SPA test 都是多重假设检验框架，用于判断"从 N 个候选中选出的最优策略是否显著优于基准"。核心思想与 CSCV 类似，但检验统计量不同。

**可行性**：
- 实现复杂度：**天级**（~1-2 天）
- White's RC 使用 bootstrap 重采样（Algorithm 3.1），需要构造 N 个策略的收益矩阵——与路径 A 一样需要参数网格
- Hansen SPA 改进了 White's RC，使用 studentized 统计量（§2, 公式 8），对噪声策略更鲁棒
- **不需要改宪法**（PBO 门替换为 RC/SPA 的 p-value 门，但门的语义相同）

**预估能否解锁双门**：与路径 A 类似，取决于策略在参数空间中的稳定性。SPA 比 CSCV 更保守（更难通过），所以如果 CSCV 能过，SPA 也能过。

**主要风险**：
- White's RC 对噪声策略敏感（Hansen 2005 §1 指出的已知问题）
- SPA 需要估计长期方差（long-run variance），对样本量敏感
- 两种方法都需要 N 个策略，本质上与路径 A 面临相同的参数网格问题
- 文献中 SPA 通常用于预测模型选择，直接套用到 walkforward 需要适配

**论文依据**：
- White (2000). "A Reality Check for Data Snooping." Econometrica, 68(5), 1097-1126. §3, Algorithm 3.1.
- Hansen (2005). "A Test for Superior Predictive Ability." Journal of Business & Economic Statistics, 23(1), 36-49. §2, 公式 (8).

---

### 路径 C：DSR + 一致性指标替代 PBO

**描述**：放弃 PBO，改用 DSR（已验证正确）+ 一个新的一致性指标（如 fold 间 SR 标准差、或 train-test rank correlation）作为第二道门。

**可行性**：
- 实现复杂度：**小时级**（~2-4 小时）
- 一致性指标候选：
  - fold 间 Sharpe Ratio 的标准差（越小越稳定）
  - train return 与 test return 的 Spearman ρ（正相关 = 一致）
  - 正收益 fold 的比例（> 60% = 通过）
- **需要改宪法** §1.3 #12：把 "PBO < 0.5" 改为新指标的阈值

**预估能否解锁双门**：取决于新指标的阈值设定。如果用 train-test Spearman ρ > 0 作为门，从当前数据看（多数 fold 正收益），很可能通过。

**主要风险**：
- 一致性指标没有 CSCV/SPA 的理论基础，是 ad hoc 选择
- 阈值设定缺乏理论依据——为什么 ρ > 0 而不是 ρ > 0.3？
- 如果未来策略退化，一致性指标可能比 PBO 更早失效
- 改宪法需要你签字

**论文依据**：无直接对应论文。closest 的理论基础是 López de Prado (2018) *Advances in Financial Machine Learning* Ch.12 "Walk-Forward" 中对 fold 间一致性的讨论，但未给出标准化检验。

---

### 路径 D：删除 PBO 门，保留 DSR 门

**描述**：最简方案。§1.3 #12 改为只检查 DSR > 1.0，不再检查 PBO。

**可行性**：
- 实现复杂度：**分钟级**（改一行宪法）
- **需要改宪法** §1.3 #12：删除 "PBO < 0.5" 条件

**预估能否解锁双门**：立即解锁（7/9 DSR > 1.0）。

**主要风险**：
- DSR 衡量的是"收益是否显著为正"，不衡量"是否过拟合"——一个策略可能 DSR 很高但完全是过拟合
- 弱化了反过拟合纪律，与项目核心价值（宪法 #7-#9）冲突
- 如果策略真过拟合，实盘会亏钱——这正是 PBO 门要防的

**论文依据**：Bailey & López de Prado (2014). "The Deflated Sharpe Ratio." Journal of Risk, 16(3). §4 讨论了 DSR 的局限性——它只纠正了 multiple testing 对 Sharpe 的膨胀，但不直接检测过拟合。

---

## 推荐

**路径 A：真 CSCV + 参数网格。**

理由：它是四条路径中唯一同时满足以下三个条件的：
1. 有严格的理论基础（Bailey et al. 2014, §3.1 Algorithm 1）
2. 不需要改宪法（PBO 门的定义和阈值不变，只是实现从伪改真）
3. 能区分"真过拟合"和"伪 PBO=75%"——如果修复后 PBO 仍然 > 50%，那是真过拟合，我们知道了真相

**实施要点**（供下一轮工单参考，本工单不实施）：
1. 参数网格：以当前活跃权重为基点，±0.1 步长，生成 N≈10-15 个变体
2. 每个变体跑一次 walkforward（复用现有 tester，只改 thresholds）
3. 构建 T×N 矩阵（T=walkforward period 数，N=变体数）
4. 按 Algorithm 1 步骤 3-5 执行 CSCV
5. S 建议取 10（与当前 fold 数一致），或取 6（减少组合数到 C(6,3)=20）

**路径 C 作为 fallback**：如果路径 A 实施中发现参数网格导致 T×N 矩阵列间相关性过高（> 0.95），CSCV 的组合退化，此时切换到路径 C（DSR + train-test ρ）。
