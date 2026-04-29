# MVP Decisions (Frozen)

Updated: 2026-04-24
Timezone: Asia/Shanghai

## Scope
- Single user only.
- Daily report only (no chatbot).
- Max news items: 20.
- Delivery target: WeChat direct chat.

## Schedule
- Send time: 22:00 (Beijing time, Asia/Shanghai).

## News Sources (4)
1. Anthropic News (official): https://www.anthropic.com/news
2. Hugging Face Blog RSS: https://huggingface.co/blog/feed.xml
3. VentureBeat AI RSS: https://venturebeat.com/category/ai/feed/
4. Marktechpost RSS: https://www.marktechpost.com/feed/

## Model
- Provider: Fireworks (OpenAI-compatible endpoint)
- Model ID: fw_JTEMiPhkR8hjPEEx663tRH

## Notes
- X and Xiaohongshu are not selected for MVP due to unstable scraping and anti-bot constraints.
- We can add them later via a dedicated connector if needed.
