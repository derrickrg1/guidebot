"""Terminal test: python scripts/ask.py "Ignite 1099" "max LTV cash-out primary 705 FICO 1.2M" """
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from guidebot import config, library
from guidebot.answer import full_doc_messages, scenario_answer
from guidebot.llm import stream_chat

if __name__ == "__main__":
    program, question = sys.argv[1], " ".join(sys.argv[2:])
    paths, _ = library.unique_docs(library.programs()[program])
    docs = [library.load(p) for p in paths]
    docs_text = "\n\n".join(library.render(d) for d in docs)
    answer = scenario_answer(question, [], library.matrices(docs), docs_text, config.CHAT_MODEL)
    if answer:
        print(answer)
    else:
        for part in stream_chat(full_doc_messages(question, docs_text, [])):
            print(part, end="", flush=True)
        print()
