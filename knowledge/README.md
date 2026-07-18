# knowledge/ — the agent's curated library

Every `.md` file in this folder is read by `research_agent.py` on every run and
injected into its prompt as "curated knowledge". Keep entries:

1. **Verified** — published papers, audited records, our own logged test results.
   No influencer claims, no unaudited screenshots, no course-marketing material.
2. **Distilled** — principles and numbers, not walls of text. The whole folder
   should stay under ~8,000 words or it crowds out the market data in the prompt.
3. **Cited** — name the source so a claim can be re-checked later.

To add material: share a link/video/article in a Claude session. Claude vets the
source, extracts the substance (video = transcript; the agent cannot watch,
only read), and writes a distilled note here — or rejects it with a reason if
it can't be verified. Files are read in filename order; use NN_ prefixes.
