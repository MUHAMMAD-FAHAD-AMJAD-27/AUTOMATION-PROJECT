# Research Archive — LLM API Aggregators / Free-Tier Routing Services

> **Research date:** 2026-08-26
> **⚠️ Staleness warning:** "Genuinely free" claims in this space go stale *fast*.
> Free tiers get pulled, flagship models move behind paywalls, relay sites exit-scam,
> and GitHub lists get disabled. Treat every "free" / model-count / uptime figure here
> as a **2026-08-26 snapshot, not permanent truth** — re-verify before relying on it,
> ideally every 2-3 months.
>
> Scope: services that aggregate, proxy, or route access to multiple LLM APIs behind one
> interface — "one API key, many models" or "find & use free AI models here."

## Contents
1. Research queries — full working set (58 actually run)
2. Proposed new queries (20, untested)
3. Query-type taxonomy (what each type finds)
4. Services & sites found (all four research streams)
5. Fraud / reliability warnings (operationally important)
6. Proposed wiring into the codebase (NOT YET APPLIED)

---

## 1. Research Queries — Full Working Set (58 run)

**Engine-compatibility caveat:** every query below was run through WebSearch (Google-backed),
which honors `site:`, `OR`, and `"quotes"`. The project's `deep_web_adapter.py` defaults to the
`ddgs`/DuckDuckGo engine, whose header explicitly says *"no site: / OR / quoted operators."*
Queries marked **Engine = S** use operators and will underperform or fail on DDG — route them to
`--engine serper`. Queries marked **D** are natural-language and DDG-safe.

**Outcome flags are inferred** from documented findings (the sub-agents logged which queries they
ran, but not per-query hit counts): ✓ = produced useful results · ~ = weak/partial · ✗ = nothing usable.

### 1a. English / global (Western stream — 14)

| Query | Type | Outcome | Engine |
|---|---|---|---|
| `free LLM API aggregator unified one API key multiple models 2026` | generic-descriptor | ✓ | D |
| `OpenRouter alternatives free tier LLM router 2026` | comparison | ✓ | D |
| `OpenAI-compatible gateway multi-provider free models proxy` | generic-descriptor | ✓ | D |
| `directory of free AI APIs unlimited free LLM models list github` | list-probe | ✓ | D |
| `Show HN free LLM API gateway aggregator launch` | community-source | ~ | D |
| `Product Hunt LLM router unified API free credits new launch 2026` | community-source | ✓ | D |
| `"free" LLM API access 500 models one key reddit LocalLLaMA` | community-source | ~ | S |
| `NanoGPT pay-as-you-go LLM API 400 models no subscription review` | brand-probe | ✓ | D |
| `Eden AI unified API 500 models free tier aggregator` | brand-probe | ✓ | D |
| `new LLM aggregator free credits signup no card 2026 "one API" unified gateway` | generic-descriptor | ✓ | S |
| `Requesty AI OR Glama AI OR Pollinations free LLM API router` | brand-probe | ✓ | S |
| `concentrate.ai LLM gateway free credits no service fee review` | brand-probe | ✓ | D |
| `AI/ML API aggregator 300 models free tier "aimlapi" OR "unify.ai" OR "notdiamond"` | brand-probe | ~ | S |
| `evolink.ai LLM gateway free credits models pricing` | brand-probe | ✓ | D |

### 1b. Chinese (15)

| Query | Type | Outcome | Engine |
|---|---|---|---|
| `大模型 API 聚合 一个 key 多个模型 免费额度` | generic-descriptor | ✓ | D |
| `OpenAI API 中转站 免费 Claude API 代理 镜像站` | generic-descriptor | ✓ | D |
| `硅基流动 SiliconFlow 免费额度 API DeepSeek Qwen 大模型` | brand-probe | ✓ | D |
| `one-api new-api 开源 大模型网关 令牌分发 中转` | brand/OSS-probe | ✓ | D |
| `DMXAPI laozhang.ai API中转 价格 支持模型 GPT Claude Gemini` | brand-probe | ✓ | D |
| `免费大模型 API 接口 GLM 智谱 免费 Kimi Moonshot 免费额度 领取` | generic-descriptor | ✓ | D |
| `API2D GPTGOD openai-hk closeai API中转 充值 GPT 国内` | brand-probe | ~ | D |
| `chatnio aiproxy 大模型 计费 分销 开源 中转平台` | brand/OSS-probe | ✓ | D |
| `API中转站 推荐 2026 gpt claude 稳定 便宜 分组 一手 渠道 三方` | generic-descriptor | ✓ | D |
| `v3api gptapi.us openai-sb 中转 跑路 稳定性 用户评价` | fraud-signal | ~ | D |
| `硅基流动 SiliconFlow 注册 送 14元 免费 额度 平台 背景 公司` | brand-probe | ✓ | D |
| `GPT_API_free chatanywhere 免费 openai key github 免费转发` | brand/OSS-probe | ✓ | D |
| `国产大模型 免费 API 额度 对比 智谱 通义千问 DeepSeek 腾讯混元 火山方舟 百炼` | comparison | ✓ | D |
| `DMXAPI 大模型 中转 一个key 所有模型 公司 背景 是否 靠谱` | fraud-signal | ~ | D |
| `V2EX linux.do 中转站 跑路 骗局 假模型 降智 warning aggregator` | fraud-signal | ✓ | D |

### 1c. India / SEA (13)

| Query | Type | Outcome | Engine |
|---|---|---|---|
| `India LLM API aggregator unified model router startup` | generic+region | ✓ | D |
| `unified AI API India free tier one key many models` | generic+region | ✓ | D |
| `Southeast Asia LLM API router aggregator Singapore inference platform` | generic+region | ✓ | D |
| `free LLM API India developers no credit card aggregator` | generic+region | ~ | D |
| `Indian AI startup API gateway pay UPI rupees GPT Claude Gemini one key` | generic+region | ✓ | D |
| `Sarvam AI Krutrim API platform models access developers` | brand-probe | ~ | D |
| `Indonesia Vietnam Thailand LLM API platform aggregator free tier developers` | generic+region | ✓ | D |
| `SEA-LION API inference free access AI Singapore playground` | brand-probe | ~ | D |
| `Vietnam AI API gateway multi-model router startup FPT VNG GreenNode inference` | brand+region | ~ | D |
| `Indonesia AI startup unified LLM API access multiple models GoTo Datasaur` | brand+region | ~ | D |
| `Japan Korea LLM API router aggregator multiple models one interface startup` | generic+region | ✗ | D |
| `airouter.in India LLM router founder review free credits` | brand-probe | ~ | D |
| `YourStory OR "Tech in Asia" OR e27 AI startup unified API access many LLM models one key India Singapore` | community-source | ✓ | S |

### 1d. Named-site verification + OSS (16)

| Query | Type | Outcome | Engine |
|---|---|---|---|
| `gorouter.app AI LLM gateway API` | brand-probe | ✗ | D |
| `OmniRoute omniroute.im self-hosted AI gateway 231 providers github` | brand-probe | ✓ | D |
| `SeekAI LLM API aggregator router free` | brand-probe | ✗ | D |
| `true-sota.com "TrueSOTA" AI API gateway free keys` | brand-probe | ✗ | S |
| `gorouter.app "new api" free llm keys claude gpt gemini` | brand-probe | ~ | S |
| `"seekai" free AI API keys aggregator reddit` | brand-probe | ✗ | S |
| `"true-sota" OR "truesota" free API keys models reddit github` | brand-probe | ✗ | S |
| `cheahjs free-llm-api-resources github awesome free llm api list` | list-probe | ~ | D |
| `seekai.app what is it education app OR llm api` | brand-probe | ✓ | S |
| `zukixa/cool-ai-stuff free reverse proxy llm api list github` | list-probe | ✓ | D |
| `gorouter.app reddit "gorouter" free claude code gpt gemini setup` | community-source | ✗ | S |
| `"gorouter.app" OR "go router app" free ai api gateway signup credits` | brand-probe | ✗ | S |
| `one-api songquanpeng vs new-api QuantumNous LLM gateway stars self-hosted` | comparison/OSS | ✓ | D |
| `Portkey gateway github open source AI gateway litellm alternative 250 providers` | brand/OSS-probe | ✓ | D |
| `awesome free llm api resources github list 2026 maintained providers rate limits` | list-probe | ✓ | D |
| `"gorouter.app" new-api register model pricing OR "sign in" AI` | brand-probe | ✗ | S |

**Working-set takeaways:** brand-probes for *confirmed* services (SiliconFlow, OmniRoute, Portkey, Eden AI)
and Chinese natural-language descriptors were highest-yield. Brand-probes for the *unconfirmed named sites*
(gorouter/seekai/true-sota) were near-total failures — signal, not noise: those belong in a one-shot
verification pass, not a recurring template. The single Japan/Korea query failed outright (see §2 fix).

---

## 2. Proposed New Queries (20 — UNTESTED, logical extensions only)

> **NONE of these were run.** They are logical variants proposed to fill gaps the research exposed
> (zero Japan/Korea coverage; thin India/SEA; no dedicated fraud-filter or comparison-multiplier buckets).
> Do not treat their value as validated — they are hypotheses to test.

**Japanese (JP) — the total gap; `ja` locale, DDG-safe:**
- `LLM API まとめ 無料 複数モデル 統合 ゲートウェイ`
- `無料 生成AI API キー 開発者 登録不要 OpenAI 互換`
- `OpenAI 互換 API 中継 無料枠 プロキシ 複数モデル`

**Korean (KR) — the total gap; `ko` locale, DDG-safe:**
- `무료 LLM API 통합 여러 모델 하나의 키 게이트웨이`
- `OpenAI 호환 게이트웨이 무료 크레딧 라우터 개발자`
- `생성형 AI API 무료 요금제 여러 모델 프록시`

**India/SEA — more angles (payment-localization + indie framing):**
- `India AI API rupee UPI billing OpenAI compatible cheap developer`
- `Indonesia LLM API rupiah pay as you go gateway multiple models`
- `Vietnam Thailand Philippines free LLM API tier developer aggregator`
- `indie hacker India launched free LLM API gateway multiple models`
- `मुफ्त AI API की डेवलपर भारत एक की कई मॉडल` (Hindi — low-confidence, tech search thin)

**Community-source (Serper — fresh launches):**
- `site:news.ycombinator.com Show HN LLM router gateway free tier`
- `site:reddit.com/r/LocalLLaMA free API aggregator many models one key`
- `site:reddit.com/r/SideProject launched LLM gateway free credits`

**Comparison-multiplier (DDG-safe):**
- `best OpenRouter alternatives 2026 free tier self hosted comparison`
- `LiteLLM vs Portkey vs new-api self hosted LLM gateway free`

**List-probe (Serper — curated lists):**
- `site:github.com awesome free llm api providers rate limits maintained`

**Fraud/reliability filter (exclusion signal):**
- `LLM API gateway relay down scam refund not working reddit review`
- `AI API 中転 サービス 停止 詐欺 返金` (JP fraud-signal variant)

---

## 3. Query-Type Taxonomy — what each type is best at

| Type | Best at finding | Notes for wiring |
|---|---|---|
| **generic-descriptor** (`free LLM API aggregator unified one key`) | Steady-state backbone — established + mid-tail services | DDG-safe, recurring. Highest ROI per slot. |
| **brand-probe** (`SiliconFlow free 额度`, `Portkey gateway github`) | Deep detail on a *known* target | Best as verification/enrichment, not discovery. Useless when the brand doesn't exist (gorouter). |
| **`site:`-scoped** (`site:github.com awesome free llm api`) | Curated lists (github), promo drops (reddit), fresh launches (HN) | **Serper-only** — DDG's `site:` is unreliable. Route to `--engine serper`. |
| **community-source** (`Show HN …`, `reddit LocalLLaMA …`, `V2EX linux.do …`) | Just-launched services before they're indexed elsewhere; real user reports | Highest freshness; noisiest. V2EX/linux.do = the CN equivalents of HN/Reddit. |
| **fraud/warning-signal** (`跑路 骗局 降智`, `exit scam refund`) | Reliability filtering — which services to *exclude* | Inverse-signal: feed a suppression/denylist, NOT the offer feed. |
| **comparison/listicle** (`OpenRouter alternatives 2026`, `X vs Y`) | Dense enumerations — one hit yields 10+ services | Great discovery multiplier; DDG-safe. |
| **list-probe** (`awesome free llm api github maintained`) | The curated GitHub lists (highest-value github_adapter targets) | Overlaps github_adapter — dedupe intent. |

---

## 4. Services & Sites Found (all four streams)

Confidence legend: **WELL-DOC** = documented, active user base · **MEDIUM** = exists but thin/marketing-heavy · **SPECULATIVE** = single-mention / unverifiable / red-flags.

### 4.1 The four originally-named sites — verdicts

| Site | Verdict | Confidence |
|---|---|---|
| **OmniRoute** (omniroute.im) | Real MIT self-hosted AI gateway, one OpenAI-compat endpoint `localhost:20128/v1`, 17 routing strategies, 4-tier fallback, token compression, MCP. `npm i -g omniroute`. BUT surrounded by **8+ near-identical GitHub forks** (MrArtt, Emredost, diegosouzapw, pitbaden, ChrisCompton, BunsDev, xrey167, yuichiinumaru) with contradictory provider counts (160/231/237/495). | Exists WELL-DOC; counts = marketing |
| **gorouter.app** | Almost certainly a hosted deployment of `QuantumNous/new-api` (SPA renders only title "New API"). NOT Flutter `go_router` / Cloud Foundry gorouter. Model list & free terms unreadable (client-side JS, zero external writeups). | Identity MEDIUM; models UNVERIFIED |
| **true-sota.com/keys** | Client-side JS app, WebFetch returns only title "TrueSOTA - AI API Gateway". Zero GitHub/Reddit/docs mentions. No provider list, no evidence it issues free keys. | SPECULATIVE |
| **SeekAI** | No LLM aggregator by this name exists. `seekai.app` = homework/education app; `sekai.app` = no-code app builder ($20M raise); `seek.ai` = enterprise text-to-SQL. Likely a misremembered name. | Not-a-router WELL-DOC; aggregator = not found |

### 4.2 Western / global hosted aggregators

| Service | URL | Model | Free access | Confidence |
|---|---|---|---|---|
| **LLM Gateway** | llmgateway.io | OSS router + hosted (AGPLv3, `theopenco/llmgateway`, ~1.6k★), 40+ providers / 200+ models | BYOK genuinely free; 5% fee on credits; self-host free | WELL-DOC |
| **CometAPI** | cometapi.com | Hosted proxy, `api.cometapi.com/v1`, 500+ models (GPT-5.6, Claude Opus 5, Gemini 3.x, Grok 4.6, DeepSeek V4, Qwen, GLM, MiniMax, Doubao, Flux) | Signup trial credits no card; else PAYG "20-40% cheaper" | WELL-DOC |
| **Eden AI** | edenai.co | Unified multimodal gateway, 500+ models / 50+ providers (France) | Free starter credits; 5.5% platform fee | WELL-DOC |
| **NanoGPT** | nano-gpt.com | Pay-per-prompt marketplace, 400+ models, no account | No free tier; $0.10 min, no deposit fee | WELL-DOC |
| **Requesty** | requesty.ai | Managed router, 600+ models / 30+ providers, routing/caching/failover | Free starter credits (amount unconfirmed) | WELL-DOC (free specifics thin) |
| **AI/ML API** | aimlapi.com | Hosted aggregator on Apache APISIX, 300-1000+ models | Free trial tier | WELL-DOC (limits vague) |
| **Router.com** | router.com | Cost-optimizing router (by Ramp, fintech) | Free through 2026 + ~$26 credits (list-price tokens) | WELL-DOC |
| **Concentrate AI** | concentrate.ai | Enterprise gateway, 150+ LLMs, governance (NY, ex-stealth Jun 2026) | $500 startup credits, no fees, no BYOK markup | Launch WELL-DOC, reports thin |
| **OrcaRouter** | orcarouter.ai | Router; Lite is MIT OSS (`Continuum-AI-Corp/OrcaRouter-Lite`), 100-200+ models (SF, May 2026) | Zero-markup BYOK + free dev credits | Launch WELL-DOC, adoption SPEC |
| **EvoLink** | evolink.ai | Hosted PAYG, 75+ models incl. image/video (Kling, Sora 2, Veo 3.1) | "Start free no card" (minimal/trial) | Product WELL-DOC, backing thin |
| **AgentRouter** ⚠️ | agentrouter.org | Proxy, 30+ models | "$100-200 free credits" — affiliate-farm signals, no owner/privacy policy | SPECULATIVE (caution) |
| **GatewayforAI** ⚠️ | gatewayforai.com | Router, 8 providers | Unknown (site 403s) | THIN-LEAD |
| **betarouter.com** ⚠️ | betarouter.com | Router, 40+ providers | Unknown (site 402s) | THIN-LEAD |

### 4.3 Chinese domestic-model platforms (genuine free access lives here)

| Platform | URL | Free access | Confidence |
|---|---|---|---|
| **SiliconFlow / 硅基流动** | siliconflow.cn | First-party inference cloud, 100+ open models (DeepSeek, Qwen3, GLM). New-user ~20M-token credit; some distilled models free-tier (~30 RPM) | WELL-DOC |
| **Zhipu / Z.AI BigModel / 智谱** | open.bigmodel.cn | GLM family; **GLM-4-Flash permanently free** (one of two "真·永久免费" domestic providers) | WELL-DOC |
| **Tencent Hunyuan / 腾讯混元** | cloud.tencent.com | The other "permanently free" domestic option (free quota on select models) | WELL-DOC (specifics MEDIUM) |
| **ModelScope / 魔搭** | api-inference.modelscope.cn/v1 | Alibaba-backed hub, ~50+ free models (MiniMax, Qwen3), registration-only + daily cap | WELL-DOC (limits MEDIUM) |
| **Alibaba Model Studio / 百炼** | help.aliyun.com/zh/model-studio | Qwen3 + resold DeepSeek/GLM; 1M free trial tokens/model (promo, not permanent) | WELL-DOC |
| **DeepSeek Open Platform** | api.deepseek.com | First-party; mostly PAID (cheap); historical free credits expired | WELL-DOC |
| **Qiniu 七牛云 AI 推理** | cnblogs.com/qiniushanghai | Domestic gateway, OpenAI+Anthropic dual-protocol, "国内直连" (marketing-adjacent source) | MEDIUM |

### 4.4 Chinese Western-model 中转 / 代理 relays (elevated fraud risk — see §5)

| Service | URL | Free access | Confidence |
|---|---|---|---|
| **laozhang.ai / 老张 API** | laozhang.ai | 200+ models, well-known in CN Claude-Code circles; usage-based paid + occasional trial | WELL-DOC |
| **DMXAPI** | dmxapi.com | Claims 300+ models, "低至6折"; advertises free OpenAI key (no documented limits); thin repo | Exists WELL-DOC, claims SPEC |
| **神马中转API / whatai** | api.whatai.cc | "650+ 模型," free trial + top-up; promoted via self-serving "awesome" repo | Service MEDIUM, reliability SPEC |
| **OpenAI-HK** | openai-hk.com | OpenAI relay, GPT/o-series/DALL-E; PAID only | WELL-DOC |
| **UniAPI** | uniapi.ai | OpenAI/Gemini/Claude 中转; PAID; thin reputation | WELL-DOC (rep thin) |
| **Chat Nio 主站** | chatnio.net | Hosted chatnio gateway (Chat UI + relay); OpenAI/Claude/Gemini/MJ + domestic; PAID | WELL-DOC |
| **Others (verify individually)** | — | CCSub `ccsub.net`, PatewayAI `pateway.ai`, allmhub, gptacg `gptacg.com`, gptapi.us | THIN / verify each |

### 4.5 India / SEA aggregators

| Service | URL | Model | Free access | Confidence |
|---|---|---|---|---|
| **Yantra AI** | apikeys.in | Router/proxy, `auto:cheap/reasoning/fast`, 350+ models / 20+ labs (named op: Aditya Borgaonkar) | No signup credits; some ₹0 models; ₹10 min | WELL-DOC (best operator disclosure) |
| **nabh.cloud** | nabh.cloud | Prepaid open-weight marketplace, 18 models (Mistral, Qwen3, Llama 3.3, GPT-OSS-120B) | **Best India free tier**: ₹50 signup credit, standing free Mistral-7B tier, monthly reset | WELL-DOC |
| **AICredits** | aicredits.in | Aggregator/proxy, OpenAI-compat failover, claims 300-400+ models | Free account no card, no free credits; ₹50 min wallet | Exists WELL-DOC, scale SPEC |
| **Velona** | velona.in | Proxy, single endpoint, "GPT-4o/Claude/Gemini/Llama +300" | Free demo, no standing credits; ₹10 min | Exists WELL-DOC, legitimacy thin |
| **AI.cc / AICC** | ai.cc | Genuine router, auto model-selection, 300-400+ models (Singapore-HQ) | Not mentioned; enterprise cost-savings pitch (claims flagged unverified by press) | Exists WELL-DOC, claims SPEC |
| **APIRoute** | useapiroute.com | Router "for Southeast Asia," Anycast edge (Jakarta/Manila/Bangkok/HCMC) | **1,000,000 free starter quota** | Exists WELL-DOC, legitimacy thin |
| **HarmonyLLM** | llm.harmonyrise.id | Router, 40+ models (GLM, DeepSeek, Qwen, Kimi, Gemini); PT Harmony Rise (Jakarta) | None; PAYG in IDR | WELL-DOC (strongest genuine-SEA indie) |
| **airouter.in** ⚠️ | airouter.in | Router (distinct from Western airouter.io) | "$1 free credits" India signup; site 403s to fetch | SPECULATIVE |
| **Opus Proxy / aiprimetech.io** ⚠️ | opus.abhibots.com / aiprimetech.io | Reseller of Claude/GPT/Gemini; crypto top-up bonus framing | grey-market reseller signals | SPECULATIVE (caution) |

**Not aggregators (context — don't miscount):** Sarvam AI, IndieRouter.ai, Krutrim (India sovereign model-makers/clouds); SEA-LION, FPT AI, GreenNode, RE:AI (SEA first-party providers). **No Japan/Korea aggregator surfaced** — real gap.

### 4.6 Open-source self-hosted routers (the durable infrastructure layer)

| Project | Repo | Notes | Confidence |
|---|---|---|---|
| **LiteLLM** | `BerriAI/litellm` | De-facto reference. 100+ providers, OpenAI format, cost tracking/guardrails/load-balancing | WELL-DOC |
| **Portkey Gateway** | `portkey-ai/gateway` | 1,600+ LLMs, 50+ guardrails. **Acquired by Palo Alto Networks (Apr 30 2026)** → Prisma AIRS; OSS core still free | WELL-DOC |
| **new-api** | `QuantumNous/new-api` (mirror gitee `QuantumNous/new-api`) | Unified hub, cross-converts to OpenAI/Claude/Gemini; billing/quota UI. **Likely what gorouter.app runs.** ~29k★ | WELL-DOC |
| **one-api** | `songquanpeng/one-api` | Original single-binary gateway new-api forked from. ⚠️ open security issue CWE-863 (#2424, unauthorized channel access) | WELL-DOC |
| **MIXAPI** | `aiprodcoder/MIXAPI` | Merges new-api + one-api + plugins; single binary/Docker | WELL-DOC (adoption MEDIUM) |
| **chatnio / coai** | `Deeptrain-Community/chatnio`, `coaidev/coai` | OSS multi-tenant Chat + gateway, 35+ providers/200+ models, billing | WELL-DOC |
| **GPT_API_free** | `chatanywhere/GPT_API_free` | Popular free/low-price OpenAI 转发 for CN; free key with caps + paid GPT-4 | WELL-DOC |
| **LLM Gateway** | `theopenco/llmgateway` | 200+ models, hosted at llmgateway.io (see §4.2) | WELL-DOC |
| **Smaller / free-provider routers** | `GXDEVS/grouter`, `AIOCANA/aiorouter-gateway`, `r2hu1/freerouter`, `openfreerouter/freerouter`, `MrFadiAi/free-llm-gateway`, `decolua/9router`, `bytonylee/free-router`, `paularlott/llmrouter`, `LMRouter/lmrouter` | OSS unified-router variants, varying activity | MEDIUM / SPEC |
| ⚠️ **Free-tier "stackers"** | `tashfeenahmed/freellmapi` (29 providers/358 endpoints ~4B tok/mo), `0xzr/freellmpool` | **Stack providers' free tiers — authors label "personal experimentation only." ToS-gray, NOT production-safe.** | avoid for prod |

### 4.7 Curated "free LLM API" GitHub lists (highest-value github_adapter targets)

| Repo | Scale | Freshness | Confidence |
|---|---|---|---|
| `ClawLabsAI/free-ai-models` | "every free AI model" | **Auto-updated every 24h** (Actions → OpenRouter/Pollinations) | strongest freshness |
| `nejib1/Free-LLM` | 34-41 providers, 110-120+ models | **Synced daily** from free-llm.com | strongest maintenance |
| `howardpen9/awesome-ai-api-proxy` | 33 gateways (CN+global+self-host) | **Reviewed 2026-07-12, prices 2026-08-16** | actively maintained |
| `open-free-llm-api/awesome-freellm-apis` | 134-444+ APIs / 40+ providers | one-click Claude Code/Cursor/Codex setup | WELL-DOC |
| `zukixa/cool-ai-stuff` | large, incl. reverse-proxies | community-notorious, "not endorsing" | WELL-DOC (entries mixed) |
| `amardeeplakshkar/awesome-free-llm-apis` | permanent-free only | no trials/no-card — **closest to system's filter criteria** | WELL-DOC |
| `mnfst/awesome-free-llm-apis` | permanent free keys | per-entry rate limits | Active |
| `vava-nessa/free-coding-models` | 170+ coding models / 15+ providers | CLI benchmark/install | MEDIUM |
| `tatn/awesome-free-ai-apis` | free APIs w/ rate limits | — | MEDIUM |
| `12britz/awesome-free-models` | free models/APIs/tools | — | MEDIUM |
| `raullenchai/free-llm-api-resources` | mirror of the now-moved cheahjs canonical | use instead of cheahjs (404) | MEDIUM |

---

## 5. ⚠️ Fraud / Reliability Warnings (operationally important — do NOT bury)

**This section is a denylist / caution register. These are the failure modes of this category.**

### 5.1 Confirmed exit-scam
- **AIProxy.io** (Singapore-operated, CN-developer-focused relay) — **CONFIRMED EXIT-SCAM.** Relay stopped
  responding late 2025, domain lapsed, prepaid balances lost. This is the canonical example of the whole
  relay category's 跑路 (run-away) risk. **Do not list, do not top up, treat as dead.**

### 5.2 The 降智 / 跑路 pattern (systemic risk across all commercial 中转 relays)
- CN dev forums (linux.do, V2EX) actively warn: **"名为'中转'实为'骗局'"** (called a "relay," actually a scam).
- **降智 / 影子API** ("shadow API / dumbing-down"): a relay advertises Claude Opus / GPT-4 but silently serves
  a cheaper substitute model. Advertised model ≠ served model.
- Selling free-tier or trial accounts as "Team/Pro."
- **Operational rule:** treat every commercial relay's model-count, uptime, and "纯血不掺水" (pure, not watered
  down) claim as **unverified vendor marketing**. Durable free access lives in first-party domestic platforms
  (§4.3: SiliconFlow, GLM-Flash, Hunyuan, ModelScope) and BYOK-free OSS routers (§4.6), NOT in the relays.

### 5.3 Affiliate-farm / referral-bait signals (Western)
- **AgentRouter** (agentrouter.org) — "$100-200 free credits, non-profit" promoted only via referral-coded
  gist articles; no named operator, no privacy/retention policy. The promoting articles themselves warn against
  production/sensitive use. **Use throwaway keys only, never production data.**
- **OmniRoute fork swarm** (§4.1) — 8+ near-identical repos with copy-paste descriptions and inflated/
  contradictory provider counts; classic star/engagement-farming signature. The tool is real; the numbers aren't.

### 5.4 GitHub repos to AVOID ingesting (do NOT add to github_adapter TARGETS)
- `cheahjs/free-llm-api-resources` — **404, repo moved/removed.** Use mirror `raullenchai/free-llm-api-resources`.
- `alistaitsacle/free-llm-api-keys` — **disabled by GitHub for ToS violation.**
- `dan1471/FREE-openai-api-keys` — **dumps of "free keys" that are almost certainly stolen/leaked. Avoid entirely** —
  ingesting or redistributing these is both a security and a legal problem.

### 5.5 ToS-gray "free-tier stackers" (not fraud, but not production-safe)
- `tashfeenahmed/freellmapi`, `0xzr/freellmpool`, and OmniRoute's Kiro/Qoder/OpenCode-Free providers work by
  stacking providers' free tiers or routing through reverse-engineered coding-assistant auth. Authors themselves
  label these "personal experimentation only." **Do not build durable infrastructure on them; do not present
  them to an audience as legitimate "free API" offers** — the upstream can cut access without warning.

---

## 6. Proposed Wiring — NOT YET APPLIED

> **STATUS: PROPOSAL ONLY. No `.py` file has been modified. Review before wiring.**
> Gating reality (as of 2026-08-26): `scheduler.py` has never run against Neon and is not in the Procfile,
> so every `deep_web_adapter.py` query below is **inert in production** until the Procfile/scheduler gap is
> fixed. The `github_adapter.py` TARGETS additions (§4.7 repos) DO reach prod today (the `worker` runs github
> every 6h). Prioritize accordingly.
### 6.1 New `QUERY_TEMPLATES` tuples (append to `adapters/deep_web_adapter.py`)

```python
# --- PROPOSED, NOT YET APPLIED (2026-08-26) --------------------------------- #
# New (category_tag, query) tuples for LLM-aggregator / router discovery.
# ENGINE NOTES:
#   * plain natural-language tuples work on BOTH ddgs (default) and Serper.
#   * tuples containing site: / OR / quotes are SERPER-ONLY — ddgs ignores or
#     mangles operators. Gate these behind the Serper path before enabling.
PROPOSED_QUERY_TEMPLATES: list[tuple[str, str]] = [
    # -- llm_aggregator (English, natural language — DDG-safe) --------------- #
    ("llm_aggregator", "free LLM API aggregator OpenAI compatible endpoint"),
    ("llm_aggregator", "free AI model router multiple providers one API key"),
    ("llm_aggregator", "unified LLM gateway free tier GPT Claude Gemini"),
    ("llm_aggregator", "OpenAI compatible proxy free access many models"),
    # -- llm_aggregator_cn (Chinese, natural language — DDG-safe) ------------ #
    ("llm_aggregator_cn", "免费 大模型 API 中转 聚合 OpenAI 接口"),
    ("llm_aggregator_cn", "免费 AI 模型 网关 多渠道 一个 key"),
    ("llm_aggregator_cn", "国内 免费 大模型 API 平台 硅基流动 智谱"),
    ("llm_aggregator_cn", "new-api one-api 自建 中转 免费 教程"),
    # -- llm_aggregator_intl (India/SEA, natural language — DDG-safe) -------- #
    ("llm_aggregator_intl", "free LLM API India developers OpenAI compatible"),
    ("llm_aggregator_intl", "free AI API gateway Indonesia Vietnam developer"),
    ("llm_aggregator_intl", "gratis API LLM Indonesia model AI OpenAI"),
    # -- aggregator_fraud (reputation checks — natural language) ------------- #
    ("aggregator_fraud", "LLM API 中转 跑路 降智 骗局 避坑"),
    ("aggregator_fraud", "AI API relay scam exit review reddit"),
    # -- free_api_lists / SERPER-ONLY (operators; gate behind Serper) -------- #
    ("free_api_lists", "site:github.com awesome free llm api list"),
    ("free_api_lists", '"free-llm-api-resources" OR "awesome-free-llm" github'),
    ("free_api_lists", "site:github.com free ai api proxy aggregator awesome"),
]
```

### 6.2 `RUN_CATEGORY_TO_TAGS` additions

```python
# --- PROPOSED, NOT YET APPLIED (2026-08-26) --------------------------------- #
# Wire the new tags into existing scheduler run-categories. No new run-category
# lane is required — reuse llm_api_drop and ai_tools (both already scheduled).
PROPOSED_RUN_CATEGORY_TO_TAGS_ADDITIONS = {
    "llm_api_drop": [  # append to existing list
        "llm_aggregator", "llm_aggregator_cn", "llm_aggregator_intl",
        "aggregator_fraud",
    ],
    "ai_tools": [      # append to existing list
        "llm_aggregator", "free_api_lists",
    ],
    # "open_source_repo": append "free_api_lists" so the OSS lane also sweeps
    #                     curated GitHub aggregator lists once/day.
}
```

### 6.3 `github_adapter.py` TARGETS additions (reaches prod TODAY — highest leverage)

```python
# --- PROPOSED, NOT YET APPLIED (2026-08-26) --------------------------------- #
# Curated aggregator lists from §4.7 that are SAFE to ingest (see §5.4 denylist
# for the ones deliberately excluded). Verify each still resolves before adding.
PROPOSED_GITHUB_TARGETS = [
    "raullenchai/free-llm-api-resources",     # mirror of removed cheahjs list
    "ClawLabsAI/free-ai-models",              # 24h auto-refresh
    "nejib1/Free-LLM",                        # daily sync
    "howardpen9/awesome-ai-api-proxy",        # reviewed 2026-07-12
    "open-free-llm-api/awesome-freellm-apis",
    # EXCLUDED (see §5.4): cheahjs/* (404), alistaitsacle/* (ToS-disabled),
    #                      dan1471/* (stolen keys).
]
```

---

*End of archive. Re-verify all "genuinely free" claims before acting on them — see the staleness note at the top.*
