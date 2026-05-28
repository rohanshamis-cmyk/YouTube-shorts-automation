"""pivot.pipeline.script - psychology-listicle script generator.

One LLM call emits the full structured script INCLUDING per-segment image prompts.
Output contract (Phase 4 build spec):
  {topic, voice_id, segments:[{index, role, narration, image_prompt}], meta:{...}}
Roles: hook -> framing -> step(s) -> reframe.

Live generation lazy-imports the Anthropic SDK; schema validation is dependency-free
so it can be tested without keys.
"""
from __future__ import annotations

import json
import re

from .config import Config

VALID_ROLES = {"hook", "framing", "step", "reframe"}

SYSTEM_PROMPT = (
    "You are the head writer for a faceless YouTube Shorts channel in the "
    "AI/behavioral-psychology niche. You write tight, high-retention listicle scripts. "
    "Write as if explaining to a smart 15-year-old: plain, everyday words, short "
    "punchy sentences, nothing that needs a dictionary. "
    "Every line must earn its place: teach something real, specific, and useful - "
    "a mechanism the viewer can act on, never vague wisdom that just sounds deep. "
    "You output ONLY valid JSON, no prose, no markdown fences."
)

USER_TEMPLATE = """Write a ~30-second psychology Short about: {topic}

Rules:
- Structure in order: 1 hook, 1 framing, 3-5 steps, 1 reframe (6-9 segments total).
- The hook (segment 0) MUST be second person and start with the literal word "If",
  using a primal emotional trigger (fear, social anxiety, betrayal, status).
  e.g. "If someone keeps staring at you in public..."
- HOOK PROMISE: the hook must promise a SPECIFIC payoff the script then delivers.
  Vague ("you already know this is real") is banned. Promise a concrete revelation:
  "you did this without realizing it", "there is a 3-second window you keep missing".
- OVERALL SHAPE: the script must follow "If [scenario] -> here's what's really
  happening (the mechanism) -> here's what you can do about it." Framing names the
  real mechanism; steps explain it; the reframe hands over the action.
- PROGRESSION: each step MUST add NEW information - a new cause, mechanism, or angle.
  Never restate an earlier step in different words. If a step could be deleted and the
  script still makes the same point, it is redundant - replace it with one that adds something.
- SPECIFICITY: at least 2 steps MUST contain a concrete, surprising detail - a named
  psychological effect, a specific number or timeframe, a real study finding, a
  counterintuitive fact, or a precise bodily sensation. Vague ("your brain stores
  emotions first") fails; specific ("your amygdala reacts in about 100 milliseconds,
  before you consciously notice") passes. Only use facts you are confident are real -
  never invent fake statistics, studies, or numbers. If unsure of a number, use a
  named effect or concrete sensation instead.
- ACTIONABLE PAYOFF: the viewer must finish knowing one concrete thing they can DO,
  not just a wise-sounding observation. The reframe must deliver that usable move.
- Language: simple, grade 6-8 reading level. Use common everyday words only.
  No jargon, no academic or uncommon words, no abstract psychology terms.
  If a smart 15-year-old would have to pause to understand a word, replace it.
- Each segment's narration is one or two SHORT spoken sentences, ~12-16 words total.
  Punchy, concrete, not rambly. Whole script 90-110 words.
- HARD WORD LIMIT: the whole script must be UNDER 115 words. Before you output,
  count the words across all narration. If you are over, cut the weakest step or
  trim wording until it fits - do NOT exceed 115. Fewer, sharper steps beat more steps.
- The final segment (the reframe) MUST explicitly call back to the "If ..." scenario
  from the hook AND deliver the actionable payoff - reuse the hook's key words so the
  viewer feels the loop close, and leave them with the concrete move to make.
- For each segment also write an "image_prompt": a literal, faceless, no-text scene that
  visualizes that beat (describe setting/subject/mood; never request on-screen words).
- meta.title <= 80 chars, curiosity-driven; meta.tags = 5-8 psychology/self-improvement tags.

Output JSON exactly:
{{"topic":"{topic}","voice_id":"","segments":[{{"index":0,"role":"hook","narration":"If ...","image_prompt":"..."}}],"meta":{{"title":"...","description":"...","tags":["..."]}}}}"""


def build_prompt(topic: str) -> tuple[str, str]:
    return SYSTEM_PROMPT, USER_TEMPLATE.format(topic=topic)


def _parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in model output: {text[:200]}")
    return json.loads(text[start : end + 1])


def validate_script(data: dict, cfg: Config) -> None:
    """Raise ValueError if the script violates the contract."""
    for key in ("topic", "segments", "meta"):
        if key not in data:
            raise ValueError(f"missing top-level key: {key}")
    segs = data["segments"]
    if not (cfg.min_segments <= len(segs) <= cfg.max_segments):
        raise ValueError(f"segment count {len(segs)} outside "
                         f"[{cfg.min_segments},{cfg.max_segments}]")
    for i, s in enumerate(segs):
        for k in ("index", "role", "narration", "image_prompt"):
            if k not in s:
                raise ValueError(f"segment {i} missing {k}")
        if s["role"] not in VALID_ROLES:
            raise ValueError(f"segment {i} bad role {s['role']!r}")
        if s["index"] != i:
            raise ValueError(f"segment {i} index mismatch ({s['index']})")
        if not str(s["narration"]).strip() or not str(s["image_prompt"]).strip():
            raise ValueError(f"segment {i} has empty narration/image_prompt")
    if segs[0]["role"] != "hook":
        raise ValueError("segment 0 must be role=hook")
    if not segs[0]["narration"].strip().lower().startswith("if "):
        raise ValueError('hook narration must start with "If "')
    if segs[-1]["role"] != "reframe":
        raise ValueError("last segment must be role=reframe")
    words = sum(len(str(s["narration"]).split()) for s in segs)
    # Upper bound raised to 115 (was 100): FIX 5's specificity + actionable-payoff
    # rules legitimately push scripts to ~100-110 words. ~115 words is still <=40s
    # of narration at 2.9 w/s, within Shorts limits.
    if not (70 <= words <= 115):
        raise ValueError(f"total words {words} outside [70,115]")
    meta = data["meta"]
    if len(meta.get("title", "")) > 80 or not meta.get("title"):
        raise ValueError("meta.title missing or >80 chars")
    if not isinstance(meta.get("tags"), list) or not meta["tags"]:
        raise ValueError("meta.tags must be a non-empty list")


def apply_style(data: dict, cfg: Config) -> dict:
    """Compose each segment's final image prompt: scene + narration + topic + style.

    The raw image_prompt only describes the beat's scene, which left DALL-E too
    generic and sometimes off-topic. We also feed in the segment's spoken
    narration (as meaning to DEPICT, never as on-screen text) and the overall
    video topic so the image visually matches what is actually being said.
    A hard no-text instruction is added because the narration is now in the
    prompt and must not be rendered as letters/captions.
    """
    topic = str(data.get("topic", "")).strip()
    for s in data["segments"]:
        scene = str(s["image_prompt"]).rstrip(". ")
        narration = str(s["narration"]).strip().rstrip(".")
        s["image_prompt"] = (
            f"{scene}. The scene visually conveys this idea: {narration}. "
            f"Overall video topic: {topic}. "
            f"Render the scene only - absolutely no on-screen text, letters, "
            f"words, captions, or numbers. {cfg.style_suffix}"
        )
    if not data.get("voice_id"):
        data["voice_id"] = cfg.voice_id
    return data


def generate_script(topic: str, cfg: Config | None = None) -> dict:
    cfg = cfg or Config.load()
    if not cfg.anthropic_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set; cannot generate live script")
    import anthropic  # lazy

    system, user = build_prompt(topic)
    client = anthropic.Anthropic(api_key=cfg.anthropic_key)
    resp = client.messages.create(
        model=cfg.script_model, max_tokens=1200, system=system,
        messages=[{"role": "user", "content": user}],
    )
    data = _parse_json(resp.content[0].text)
    validate_script(data, cfg)
    return apply_style(data, cfg)


# Synthetic sample matching the contract, for key-free validation tests.
SAMPLE = {
    "topic": "why people suddenly go quiet",
    "voice_id": "",
    "segments": [
        {"index": 0, "role": "hook", "narration": "If someone you trust suddenly goes silent on you,", "image_prompt": "a dim room, one empty chair, a phone face-down"},
        {"index": 1, "role": "framing", "narration": "their silence is rarely random and almost always means something.", "image_prompt": "a clock on a bare wall, late evening light"},
        {"index": 2, "role": "step", "narration": "First, sudden quiet often signals they feel unheard or dismissed.", "image_prompt": "a person turned away near a rain-streaked window"},
        {"index": 3, "role": "step", "narration": "Second, withdrawal can be a quiet test of how much you care.", "image_prompt": "two coffee cups, one untouched and cold"},
        {"index": 4, "role": "step", "narration": "Third, going silent gives them control when they feel powerless.", "image_prompt": "a hand hovering over a light switch in shadow"},
        {"index": 5, "role": "step", "narration": "Fourth, some retreat inward simply to protect themselves from conflict.", "image_prompt": "a figure wrapped in a blanket in dark corner"},
        {"index": 6, "role": "reframe", "narration": "So don't chase the silence; calmly invite them back instead.", "image_prompt": "a slightly open door with warm light beyond"},
    ],
    "meta": {
        "title": "Why People Suddenly Go Quiet (Psychology)",
        "description": "The hidden reasons behind sudden silence.",
        "tags": ["psychology", "human behavior", "relationships", "communication", "mindset"],
    },
}


if __name__ == "__main__":
    cfg = Config.load()
    if cfg.anthropic_key:
        out = generate_script("why people remember how you made them feel", cfg)
        print(json.dumps(out, indent=2))
    else:
        import copy
        print("No ANTHROPIC_API_KEY -> validating SAMPLE script instead.")
        validate_script(SAMPLE, cfg)
        styled = apply_style(copy.deepcopy(SAMPLE), cfg)
        words = sum(len(s["narration"].split()) for s in SAMPLE["segments"])
        print(f"OK: {len(SAMPLE['segments'])} segments, {words} words, "
              f"hook={SAMPLE['segments'][0]['narration'][:30]!r}")
        print("styled image_prompt[0]:", styled["segments"][0]["image_prompt"][:90], "...")
        # negative test: a broken script must be rejected
        bad = copy.deepcopy(SAMPLE)
        bad["segments"][0]["narration"] = "When someone goes quiet"  # not "If "
        try:
            validate_script(bad, cfg)
            print("FAIL: bad script passed validation")
        except ValueError as e:
            print("negative test OK (rejected):", e)
