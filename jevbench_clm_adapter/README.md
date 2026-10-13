# CLM 在 jev-bench 上的完整评测（虚拟环境）

上游项目：[Running-Dolphins/jev-bench](https://github.com/Running-Dolphins/jev-bench)，本包提供**独立适配器**，不修改上游脚本、不调用 Jev API、不要求 Jev API Key，也不覆盖 Jev 历史结果。

## 1. 前提与注意

- 你已经在 `/root/CLM/.venv-clm-t4` 中启动：GPU 3、4 的 Qwen3-8B vLLM pooling (`http://127.0.0.1:8090`)；CPU CLM head (`http://127.0.0.1:8700`)。
- 必须确认 `POST /v1/systemone` 真实推理成功；`/health` 单独成功不足以代表完成部署。
- 测试脚本本身只使用 Python 标准库、CPU，不会再加载模型，也不会占用 GPU 0–2。
- 访问数据源需能够连通公开 GitHub Raw 和 `datasets-server.huggingface.co`。上游 jev-bench 自动下载并缓存数据在 `data/`；部分语料可能受许可约束。
- 考虑 T4 16GB 以及既有 FlashInfer/EngineCore 故障，任务逐条执行，并允许断点恢复；不要直接使用上游 `JEV_WORKERS=8`。
- `jev-bench` 是由第三方社区提供，数据样本、任务、提示词和指标均沿用其当前版本；并非 CLM 作者官方结果。

## 2. 获取源代码

```bash
cd /root/CLM
source .venv-clm-t4/bin/activate
# 公共仓库。无 GitHub SSH Key 时请用 HTTPS：
git clone https://github.com/Running-Dolphins/jev-bench.git
cd jev-bench
python jevbench.py list
```

从本包把 `clm_jevbench.py` 放到此目录，与 `jevbench.py` 平级，例如：

```bash
cp /root/CLM/jevbench_clm_adapter/clm_jevbench.py /root/CLM/jev-bench/clm_jevbench.py
```

## 3. 核心端到端检查（不调用 Jev）

```bash
cd /root/CLM/jev-bench
python clm_jevbench.py check
python clm_jevbench.py list
```

`check` 会检查 CLM 的 `/health` 和 `/v1/models`，并实际执行一条两选一的 `/v1/systemone` 请求。
如 CLM 服务设置了 `CLM_API_KEY`，请先在当前终端 `export CLM_API_KEY=...`。

## 4. 从小样本逐步扩大

```bash
# 先用最简单的二分类 10 条热身
python clm_jevbench.py run sms-spam --n 10 --out clm_runs_smoke10

# 然后运行多选意图，检验较多候选项是否稳定
python clm_jevbench.py run banking77 --n 20 --out clm_runs_smoke20

# 测试一个 500 条任务
python clm_jevbench.py run sms-spam --n 500 --out clm_runs_500

# 全部 12 个任务 x 每任务 500 条 = 6000 条
python clm_jevbench.py run all --n 500 --timeout 300 --retries 3 --out clm_runs_500

# 假如中途网络故障/8090 推理崩溃，修好后直接重复同一命令，自动跳过已成功样本
python clm_jevbench.py run all --n 500 --timeout 300 --retries 3 --out clm_runs_500
```

⚠ **断点续跑限制**：任务 `sms-spam --n 10` 与 `sms-spam --n 500` 数据采样不同，不能写到同一个 `--out` 下。使用独立输出目录避免样本集冲突：

```bash
python clm_jevbench.py run sms-spam --n 10 --out clm_runs_smoke
python clm_jevbench.py run banking77 --n 20 --out clm_runs_smoke20
python clm_jevbench.py run sms-spam --n 500 --out clm_runs_500
python clm_jevbench.py run all --n 500 --out clm_runs_500
```

后续 `--out clm_runs_500` 始终保持相同，支持断点续跑。每种 n/模型/问题组合请用独立输出目录。

如果想先验证脚本能读取公开数据但不向模型发请求：

```bash
python clm_jevbench.py run sms-spam --n 5 --dry-run
```

`--dry-run` 是随机模拟预测，不可作为模型评测结果。

## 5. 输出报告

```text
jev-bench/
├── jevbench.py                    # 原仓库；不修改
├── results/                       # Jev 已发布历史指标；不覆盖
├── clm_jevbench.py                # 本适配器
└── clm_runs_500/
    ├── aggregate.json            # 本轮运行任务汇总
    ├── comparison_clm_vs_published_jev.csv
    ├── raw/
    │   ├── banking77.jsonl       # 含数据原文和预测，断点续跑
    │   └── banking77.meta.json   # 数据+问题+模型指纹
    ├── predictions/
    │   └── banking77.jsonl       # 不包含原文的精简结果
    └── results/
        └── banking77.json        # Accuracy / ECE / Brier / 置信度分桶等
```

`comparison_clm_vs_published_jev.csv` 为 Excel 兼容的 UTF-8 BOM 编码，可以直接使用 Excel/WPS 打开；可用：

```bash
python clm_jevbench.py compare --out clm_runs_500
python -m json.tool clm_runs_500/results/banking77.json
```

原始评分指标包括：Accuracy，10-bin ECE，multiclass Brier，p50/p90 延迟，输入 Token，总体错误率；在 0.5/0.7/0.8/0.9/0.95/0.99 置信度阈值下的 coverage、选择性准确率、透过错误数；可靠性分桶；高/低置信度门槛诊断；置信度区别对错的 AUROC。

**成本提醒**：原仓库的 `$ / 1000` 是 Jev 云 API 官方计价，而你的 CLM 为本地自托管。适配器有意删除这个价格字段，不把本地 GPU 运营成本错误设为 $0。

## 6. 可选：运行六种实验

```bash
python clm_jevbench.py experiment options --n 100 --out clm_runs_exp100
python clm_jevbench.py experiment order --n 100 --out clm_runs_exp100
python clm_jevbench.py experiment repeat --n 100 --out clm_runs_exp100
python clm_jevbench.py experiment language --n 100 --out clm_runs_exp100
python clm_jevbench.py experiment oos --n 100 --out clm_runs_exp100
python clm_jevbench.py experiment descriptions --n 100 --out clm_runs_exp100
```

- 实验分别测候选项数量、候选顺序、重复稳定性、跨语言、OOS 和候选描述方式。
- 实验沿用原仓库公式；与标准 12 个任务不同，实验**不支持逐条断点续跑**，失败请重新执行。
- `x_repeat` 在同一服务上存在向量缓存，重复结果可能高度一致；这代表缓存条件下的稳定性，而非每次绕过缓存的独立推理。

## 7. 公平对比说明

原仓库 `results/*.json` 记录了 **2026-09-20 Jev 的一轮历史实验**。适配器可以输出 CLM vs Jev 的描述性差值（准确率百分点、ECE、coverage 等），但：

1. 必须核对测试集采样、版本和任务提示词是否一致；固定随机种子本身并不保证跨日期的数据源版本一致。
2. 远端 Jev 与本机双 T4 CLM 的延迟混有网络和硬件差异，不可直接解释为模型本身的速度优势。
3. 同一批数据可能与模型预训练存在交集；公开集不等于严格盲测集。
4. 部分任务包含 77/100/150 个候选项，GPU 推理负载比二分类大；先完成少量试跑。
5. 本脚本不会在用户服务器上实际运行；所有指标需以你自己的执行结果为准。

## 8. 常见错误

- `Connection refused 127.0.0.1:8700`：CLM API 未启动。
- `embedder false`：CLM 无法连接 vLLM 8090。
- `EngineCore encountered an issue`：看 vLLM 终端中最早的异常堆栈，曾出现 GCC/FlashInfer/Triton 报错；不要只看 8700 的 `/health`。
- `HTTP 502`：CLM 上游 Embedding 失败；修复 8090 后再次运行，已完成数据不会丢失。
- Hugging Face datasets-server 不可访问：需要数据源网络访问，或提前在 `data/` 放置上游标准缓存；本适配器不提供伪造替代数据。
- `Existing ... checkpoint mismatches`：--n/任务定义/模型发生变化，使用另一个 `--out`，避免混用不同测试集。
- `KeyError` / `unknown label`：任务数据或上游代码发生变化，先检查源数据标签映射；脚本不会擅自更换标签。
