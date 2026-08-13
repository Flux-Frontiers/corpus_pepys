# User Guide — The Pepys Chat App

The easiest way to explore Samuel Pepys' diary is the **chat app** — a browser
interface where you ask questions in plain English and get back the diary entries
that answer them, optionally summarised into a narrative reply.

No command line, no JSON. Just type a question and read.

---

## Starting the app

You need two things running: the **worker** (which holds the diary index) and the
**chat app** (the browser front end).

```bash
make run     # starts the worker on http://localhost:8000
make chat    # opens the chat app at http://localhost:8501
```

Your browser opens automatically to the chat interface. Leave the worker running
in the background — the app talks to it for every question.

> The worker must be running before you open the chat app. If you see a connection
> error, run `make run` first and reload the page.

---

## Asking a question

Type anything into the box at the bottom — **"Ask about Pepys' world…"** — and press
Enter. You don't need keywords or exact phrases; ask the way you'd ask a historian:

- *What did Pepys witness during the Great Fire of London?*
- *How did the plague change daily life in the city?*
- *Was Pepys happy in his marriage?*
- *What did he think of the King?*

The app searches all 7,282 diary passages by **meaning**, not just word matching,
so a question about "the epidemic" will still surface entries about the plague.

### Not sure where to start?

The sidebar has a **💡 Try asking** section with eight ready-made questions covering
the Great Fire, the plague, the Navy Office, music, the court, the theatre, money,
and the Dutch War. Click any one to run it instantly.

---

## Reading the results

For each question you'll see:

1. **An answer** *(if "Generate answer" is on)* — a short narrative reply written
   from the diary entries themselves, quoting real dates and passages.
2. **A passage count** — e.g. *📊 8 matching passages found*.
3. **The diary entries** — click **📄 Diary entries** to expand the source
   passages. Each card shows the entry, its date, and a **relevance bar**:
   - 🟢 green = strong match
   - 🟠 amber = moderate
   - 🔴 red = weak

The entries are always the ground truth. The generated answer is a convenience
built on top of them — if you want to read Pepys in his own words, open the cards.

---

## Settings (sidebar)

| Setting | What it does |
|---|---|
| **Results** | How many diary passages to retrieve (1–20, default 8). Raise it for broad questions, lower it for sharp ones. |
| **Min score** | Hides individual passages below a relevance threshold. Raise it to cut weak matches. |
| **Semantic floor** | If even the best match is weak, show nothing rather than guess. Useful to avoid forced answers to off-topic questions. |
| **Generate answer** | Toggles the narrative reply. Off = just the passages (instant). On = a written summary (needs Ollama — see below). |
| **🗑️ Clear chat** | Wipes the conversation and starts fresh. |

The **Worker URL** and **Secret** fields at the top only matter if you're pointing
the app at a remote worker or one protected with a shared secret. For local use,
leave them as they are.

---

## Turning on written answers

By default the app returns diary passages only — fast and fully offline. To get a
**narrative answer** written from those passages, you need a local LLM server
running and the **Generate answer** toggle on.

[Ollama](https://ollama.com) is the simplest option and runs on Linux, macOS and
Windows with no API key:

```bash
ollama pull qwen3:4b
cp docker/.env.example docker/.env
```

Then set these in `docker/.env` and restart the worker (`make down && make up`):

```bash
VLLM_ENDPOINT_URL=http://host.docker.internal:11434/v1
VLLM_MODEL=qwen3:4b
VLLM_API_KEY=
```

Now flip **Generate answer** in the sidebar. Questions return a written reply
grounded in the retrieved entries, with the source passages still available below.

You can also switch backend from the sidebar's **Provider** dropdown without
touching `docker/.env` — *Ollama*, *oMLX*, or *OpenAI*.

> **On Apple Silicon?** [oMLX](https://omlx.ai) is faster — answers in about a
> second. `make serve-llm` starts it on port 8080, and the shipped
> `docker/.env.example` already points there; you only need to set `VLLM_API_KEY`
> to your oMLX key. It is macOS/M-series only.

> With no LLM configured at all, leave **Generate answer** off — the passage view
> works on its own and is the fastest way to read the diary.

Full settings for every backend: [API Reference](API.md#llm-synthesis).

---

## Tips

- **Ask follow-ups.** The app keeps your conversation visible, so you can work
  through a topic question by question.
- **Open the passages.** The real reward is Pepys' own voice — the generated
  answer is a map, the entries are the territory.
- **Widen, then narrow.** Start with a broad question and a high **Results** count
  to see the landscape, then ask something specific about what you find.

---

For programmatic access (curl, scripting, your own UI), see the
[API Reference](API.md).
