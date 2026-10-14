"""从当前心情、人物与近期经历启动联想召回，筛选记忆强度与可说范围。
默认加固被想起的痕迹；只读场景可显式关闭加固。"""


from __future__ import annotations

from dataclasses import dataclass, field

from .affect import Mood
from .assoc import AssocGraph, rank, seeds_from_now, spread
from .decay import FORGOTTEN, MemoryStore
from .visibility import Viewpoint, can_speak, visibility_of_scene
from .retrieval import relevance, scene_text, query_seeds, scene_identity, stamp


@dataclass
class Her:
    mood: Mood = field(default_factory=Mood)

    with_person: str = ""

    viewpoint: Viewpoint = field(default_factory=Viewpoint)

    recent_scenes: list[str] = field(default_factory=list)
    query: str = ""
    mentioned_people: list[str] = field(default_factory=list)
    exclude_texts: list[str] = field(default_factory=list)
    share_details: bool = False


@dataclass
class Recalled:
    key: str
    text: str
    score: float
    speakable: bool
    why_not: str = ""
    scene: object = None

    def render(self) -> str:
        if self.speakable:
            return render_memories([self.text])
        why = (self.why_not or "私下里的事").replace("在私聊里说的", "私下里说的")
        return (f"我还想起{self.text}——那是{why}，"
                f"当前会话不在这段私下往来的可公开范围内，我保留隐私边界，如何回应仍由我决定")


_LEADS = ("还有，", "对了，", "我想起，", "另外，", "嗯，还有——")


def render_memories(items: list[str]) -> str:
    if not items:
        return ""
    out = []
    for i, t in enumerate(items):
        t = (t or "").strip()
        if not t:
            continue
        if i == 0:
            out.append("我想起来，" + t)
        else:
            out.append(_LEADS[(i - 1) % len(_LEADS)] + t)
    return "".join(p + ("" if p.endswith("。") else "。") for p in out)


@dataclass
class RecallResult:
    recalled: list[Recalled] = field(default_factory=list)

    unreachable: int = 0
    seeds: dict[str, float] = field(default_factory=dict)

    @property
    def speakable(self) -> list[Recalled]:
        return [r for r in self.recalled if r.speakable]

    @property
    def withheld(self) -> list[Recalled]:
        return [r for r in self.recalled if not r.speakable]

    def render(self) -> str:
        if not self.recalled:
            return ""
        parts = ["【我想起的过去经历】\n我保留这些经历原有的人物、时间与来源；这是我的回忆，是否谈起由我决定。"]
        for r in self.recalled:
            scene = r.scene
            source = f"{stamp(scene.start)}；人物 {scene_identity(scene)}；来源 {'、'.join(sorted(scene.sessions))}"
            boundary = "；这是私下得知的事，我记得不等于适合向当前对象转述" if not r.speakable else ""
            parts.append(f"我对那段经历的回忆（{source}{boundary}）：{r.text}")
        return "\n".join(parts)

    def explain(self) -> str:
        return (f"想起 {len(self.recalled)} 件"
                f"（能说 {len(self.speakable)} / 不说 {len(self.withheld)}），"
                f"另有 {self.unreachable} 件淡到够不着")


def remember(her: Her, graph: AssocGraph, store: MemoryStore, *,
             top_k: int = 5, now: float | None = None,
             reinforce: bool = True) -> RecallResult:
    """联想候选经过强度与内容筛选后附带可说范围，按开关加固所选记忆。"""
    seeds = seeds_from_now(her.mood, person=her.with_person,
                           recent_scenes=her.recent_scenes)
    topic_seeds = query_seeds(store.scenes, her.query)
    seeds.update(topic_seeds)
    for person in her.mentioned_people:
        seeds[f"person:{person}"] = 1.0
    if not seeds:
        return RecallResult()


    act = spread(graph, seeds, hops=2)


    picked = rank(graph, act, her.mood, top_k=len(store.scenes))
    def score_candidate(item):
        key, base, _ = item
        scene = store.scenes[key]
        trace = store.traces.get(key)
        strength = min(1.0, trace.current(now)) if trace else 1.0
        return (base + relevance(her.query, scene_text(scene), scene.topic)) * (0.4 + 0.6 * strength)
    picked.sort(key=score_candidate, reverse=True)


    out: list[Recalled] = []
    unreachable = 0
    recall_keys: list[str] = []

    for key, score, narr in picked:
        trace = store.traces.get(key)
        scene = store.scenes.get(key)
        if topic_seeds and relevance(her.query, scene_text(scene), scene.topic) <= 0:
            continue


        if trace is not None and trace.forgotten(now):
            unreachable += 1
            continue


        text = (getattr(scene, "narrative", "") or "").strip()
        if not text:
            text = (getattr(scene, "meaning", "") or
                    getattr(scene, "peak_text", "") or "").strip()
        if not text:
            continue
        if any(text in t for t in her.exclude_texts if t):
            continue
        corrections = [m.understanding for m in scene.moments
                       if m.understanding and m.understanding.was_wrong]
        if corrections:
            u = corrections[-1]
            text = f"我当时的理解是：{u.read_as[:100]}；后来收到的纠正原话是：{u.corrected_by[:140]}。"
        if her.share_details:
            details = [f"我那时在做：{scene.doing}" if scene.doing else ""] + [f"我当时留意到：{x}" for x in scene.noticed[:2]]
            text += "\n" + "；".join(x for x in details if x)

        vis = visibility_of_scene(scene) if scene is not None else 1
        ok, why = can_speak(vis, her.viewpoint)
        if vis == 0 and her.viewpoint.umo and her.viewpoint.umo not in scene.sessions:
            ok, why = False, "其他私聊中的往来"
        out.append(Recalled(key=key, text=text[:400], score=score_candidate((key, score, narr)),
                            speakable=ok, why_not=why, scene=scene))
        recall_keys.append(key)
        if len(out) >= top_k:
            break


    if reinforce and recall_keys:
        store.recall(recall_keys, now)

    return RecallResult(recalled=out, unreachable=unreachable, seeds=seeds)
