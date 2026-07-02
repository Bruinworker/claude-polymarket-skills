# claude-polymarket-skills

> Stop guessing the game. Find the wallet that's up $4M, read its ENTIRE book, and mirror it with your $100.

Two [Claude Code](https://claude.com/claude-code) skills that turn one Polymarket soccer game into a ready-to-fire trade ticket. Read-only — no keys, no wallet, no execution. Just public APIs and cold math.

**English** | [中文](#中文)

---

## The idea

Everyone screenshots the leaderboard. Almost nobody checks what the winners are *actually* holding — across every market of the game, at what entry, hedged or naked.

`/poly-bet-plan spain vs austria` does exactly that:

1. **discover** — crawl the top holders of every core market in the game
2. **score** — rank every wallet by all-time PnL and margin (lb-api). SHARP / FISH / MM tiers
3. **full book** — pull each candidate's **entire position in the game**, every market including exact score, with average entry and running PnL. Market-maker and hedge books get exposed here and thrown out — a wallet that looks like a genius on one market is often just inventory exhaust
4. **mirror** — the top clean sharp's net book, scaled to your budget. Full deployment, min leg $10, no kelly cosplay
5. **execute** — CLOB entry band and slippage per leg, printed right next to the price the sharp got in at. If your fill is much worse than theirs, the ticket tells you

Then the agent has to argue *against* its own ticket (is the target really sharp? is the entry gone? will the book even fill?) before it's allowed to print it.

## What you get

| bet | price | stake | shares | payout if hit |
|---|---|---|---|---|
| 🥇 No — Spain win | 25.8¢ | **$55** | 213.6 | **$213.59** |
| 🥈 Yes — Austria win | 8.0¢ | **$45** | 562.5 | **$562.50** |

…plus the mirror target's full book with entries, a self-rebuttal, per-leg execution notes, and two charts:

<p>
  <img src="docs/example-distribution.png" width="420" alt="implied exact-score distribution">
  <img src="docs/example-pnl.png" width="420" alt="ticket PnL per final score">
</p>

The second skill, **poly-state-scan**, is the engine underneath: it reads the exact-score grid as Arrow-Debreu state prices and prices every bundle market (moneyline, totals, spreads, BTTS…) by replication. Run it standalone to see the score distribution the market actually believes.

## Install

Python 3.10+, `matplotlib` for charts (`certifi` recommended on macOS).

```bash
git clone https://github.com/Bruinworker/claude-polymarket-skills.git
cd claude-polymarket-skills
./install.sh          # copies both skills into ~/.claude/skills/  (--symlink to track the repo)
pip install matplotlib certifi
```

Restart Claude Code, then:

```
/poly-bet-plan spain vs austria
/poly-state-scan psg vs bayern
```

No Claude? The pipelines are plain Python CLIs:

```bash
python3 skills/poly-bet-plan/plan.py "spain austria" --budget 100 --out /tmp
python3 skills/poly-bet-plan/plan.py <game> --wallet 0xABC...   # force a mirror target
python3 skills/poly-state-scan/scan.py "spain austria" --out /tmp
```

## Fine print

Research tooling, not financial advice. Sharps can be wrong; mirroring them at a worse entry can be negative-EV even when they're right; prediction markets can eat your whole stake. Know your jurisdiction's rules. MIT license.

---

<a name="中文"></a>

# 中文

> 别猜比分了。找到这场里赚了 $400 万的钱包,把它的完整持仓翻出来,用你的 $100 原样抄一份。

两个 [Claude Code](https://claude.com/claude-code) 技能,把一场 Polymarket 足球赛变成一张能直接下的交易票。纯只读——不碰私钥、不碰钱包、不下单,只有公开 API 和冷冰冰的数学。

## 思路

人人都在截图排行榜,几乎没人去查赢家**真正拿着什么**——这场比赛的每一个盘、什么价位进的、有没有对冲。

`/poly-bet-plan spain vs austria` 干的就是这件事:

1. **发现** — 爬这场每个核心盘的大户名单
2. **打分** — 按全期盈亏和利润率给每个钱包分层(lb-api):SHARP / 鱼 / 做市商
3. **拉全仓** — 把每个候选钱包**在这场比赛的完整持仓**全部拉出来,含精确比分盘、进场均价、浮动盈亏。做市商和对冲仓在这一步现形并被剔除——单看一个盘像天才的钱包,常常只是做市库存的边角料
4. **镜像** — 最强干净 sharp 的净持仓,按比例缩放到你的预算。全额进场,最小腿 $10,不搞 kelly 玄学
5. **执行核对** — 每条腿查订单簿深度和滑点,和 sharp 自己的进场价并排打印。你的成交价比他差太多,票上会直说

最后 agent 还必须先反驳自己的票(目标真是 sharp 吗?入场价还在吗?订单簿吃得下吗?)才允许输出。

## 你拿到什么

| 注 | 价 | 投入 | 份数 | 命中回款 |
|---|---|---|---|---|
| 🥇 No — 西班牙胜 | 25.8¢ | **$55** | 213.6 | **$213.59** |
| 🥈 Yes — 奥地利胜 | 8.0¢ | **$45** | 562.5 | **$562.50** |

外加镜像目标的完整持仓(带进场价)、一段自我反驳、每条腿的执行备注、两张图(隐含比分分布 + 每个比分下的票据盈亏)。

第二个技能 **poly-state-scan** 是底层引擎:把精确比分盘读成 Arrow-Debreu 状态价格,用复制定价法给每个组合盘(胜平负、大小球、让球、BTTS……)定价。单独跑它,能看到市场真正相信的比分分布。

## 安装

Python 3.10+,画图需要 `matplotlib`(macOS 建议装 `certifi`)。

```bash
git clone https://github.com/Bruinworker/claude-polymarket-skills.git
cd claude-polymarket-skills
./install.sh          # 拷进 ~/.claude/skills/(--symlink 则跟随仓库更新)
pip install matplotlib certifi
```

重启 Claude Code,然后:

```
/poly-bet-plan spain vs austria
/poly-state-scan psg vs bayern
```

不用 Claude 也行,脚本本身就是完整的命令行工具(用法见上面英文部分)。

## 说清楚

研究工具,不是投资建议。sharp 也会错;就算他对,你在更差的价位跟单也可能是负 EV;预测市场能把本金全吃掉。注意你所在地区的合规要求。MIT 协议。
