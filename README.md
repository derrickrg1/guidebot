# Guideline Assistant

Chat with your guidelines and matrices. Runs 100% locally on Ollama.

## Two kinds of answers

**Scenario questions** ("max LTV, cash-out, investment, 705 FICO, $1.2M"):
1. The AI maps your deal onto the matrix (which row, which column). It never states a number itself.
2. Python looks up the exact cell. FICO rounds down to its tier, and the loan amount lands in its bracket.
3. The AI lists overlays that apply (condo, 2-4 units, IO, FTHB, reductions), each with a quote from the doc.
4. Python applies them (caps take the lower number, reductions subtract) and shows every step.
5. Each quote is checked word for word against the doc. ✅ = found, ⚠️ = check it yourself.

**Everything else**: the AI reads the program's complete guidelines, not snippets, and has to cover
every condition, footnote, and reduction that applies.

## Verification (the "is it legit" part)

Sidebar > **Verify Documents**. Each page shows the original PDF next to exactly what the bot reads.
Matrices are editable grids. Fix anything off, then hit **Approve**.

- LTV numbers are blocked for any doc you haven't approved.
- If a PDF gets updated, it flips back to unverified automatically.
- Exact duplicate files are detected and skipped.

## Setup (Windows)

```
ollama pull gemma4:26b
ollama pull nomic-embed-text
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run ui/app.py
```

Docs go in `docs/<Program>/`. Pick a program in the sidebar for the best answers.
"All programs (search)" is for cross-program questions and needs **Update index** first.

## Handy

- `python scripts/ask.py "Ignite 1099" "max LTV cash-out primary 705 FICO 1.2M"`
- `pytest`
- Settings: `guidebot/config.py`
