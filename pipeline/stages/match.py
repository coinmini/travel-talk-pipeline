"""Stage 5: match cleaned VO segments to face/B-roll picture plan.

核心目标：
1. 低露脸（默认 ~12%）
2. 口播关键词 ↔ B-roll 标签语义对齐（水鸟→水鸟镜头，河马→河马镜头）
3. 两轮分配：先锁「强景物句」专用镜头，再填其余
4. 整句硬切；B-roll 禁止复用；禁止冻帧硬撑
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..utils import write_json

# 弱景物： alone 不足以强制盖画（感悟句里「湖上」很常见）
WEAK_SCENIC = {"湖", "湖面", "水面", "水草", "天空", "草原", "马赛马拉"}

# 强景物：必须尽量盖到对应标签，优先占用高光 B-roll
STRONG_SCENIC = {
    "河马",
    "海马",
    "水鸟",
    "鸟",
    "飞翔",
    "飞过",
    "耳朵",
    "眼睛",
    "长颈鹿",
    "斑马",
    "倒影",
    "船",
    "乘船",
    "小船",
    "日落",
    "树",
    "海",
    "海水",
    "海边",
    "沙滩",
    "青山",
    "山",
    "游泳",
    "滑梯",
    "滑板",
    "滑墙板",
    "水上",
    "渔民",
    "渔船",
    "阳光",
    "展会",
    "展位",
    "设备",
    "人工智能",
    "AI",
    "供应商",
    "客户",
    "团队",
    "物理世界",
    "聊天框",
    "数字世界",
}
# 主体词：有则必须命中其一（不能只用「树」糊弄长颈鹿句）
PRIMARY_SCENIC = {
    "河马",
    "海马",
    "水鸟",
    "鸟",
    "飞翔",
    "飞过",
    "耳朵",
    "眼睛",
    "长颈鹿",
    "斑马",
    "倒影",
    "船",
    "乘船",
    "小船",
    "日落",
    # 海岛
    "海",
    "海水",
    "游泳",
    "滑梯",
    "滑板",
    "滑墙板",
    "渔民",
    "渔船",
    # 展会 / AI
    "展会",
    "展位",
    "设备",
    "人工智能",
    "AI",
    "供应商",
    "物理世界",
}


def _stage_for_text(text: str, stages: list[dict], default_face: float) -> tuple[str, float]:
    best_id = "body"
    best_score = 0
    best_face = default_face
    for st in stages:
        score = sum(1 for kw in st.get("keywords") or [] if kw in text)
        if score > best_score:
            best_score = score
            best_id = st.get("id") or "body"
            best_face = float(st.get("face_ratio", default_face))
    return best_id, best_face


def _norm_text(text: str) -> str:
    return (
        (text or "")
        .replace("海马", "河马")
        .replace("漏出", "露出")
        .replace("风貌", "丰茂")
    )


def _extract_match_keys(text: str, aliases: dict[str, list[str]]) -> list[str]:
    keys: list[str] = []
    norm = _norm_text(text)
    raw = text or ""
    for k, syns in aliases.items():
        if k in norm or k in raw:
            if k not in keys:
                keys.append(k)
            continue
        for s in syns:
            if s and (s in norm or s in raw):
                if k not in keys:
                    keys.append(k)
                break
    for k in STRONG_SCENIC | WEAK_SCENIC:
        if k in norm and k not in keys:
            keys.append(k)
    return keys


def _text_hit(text: str, patterns: list[str]) -> bool:
    if not text or not patterns:
        return False
    return any(p and p in text for p in patterns)


def _tag_hit(tags: set[str], key: str, aliases: dict[str, list[str]]) -> bool:
    """关键词 → 标签命中。注意：河马不能用泛「动物」糊弄。"""
    syns = set(aliases.get(key) or []) | {key}
    if key in ("河马", "海马"):
        syns = {"河马", "海马"}  # 严格
    elif key in ("耳朵", "眼睛"):
        syns = {"河马", "海马"}  # 露出耳/眼 → 河马镜头
    elif key in ("水鸟", "鸟", "飞翔", "飞过"):
        syns = {"水鸟", "鸟"}
    elif key in ("船", "乘船", "小船"):
        # 不含「渔船/渔民」，避免普通海景船图抢「渔民」槽
        syns = {"船", "乘船", "救生衣", "小船"}
    elif key in ("渔船",):
        syns = {"渔船", "渔民", "码头"}
    elif key in ("倒影",):
        syns = {"倒影", "树", "空镜", "风景"}
    elif key in ("树",):
        syns = {"树", "倒影", "空镜"}
    elif key in ("长颈鹿",):
        syns = {"长颈鹿"}
    elif key in ("斑马",):
        syns = {"斑马"}
    elif key in ("海", "海水", "海边", "蓝"):
        syns = {"海", "海水", "海边", "沙滩", "海岸", "空镜", "风景"}
    elif key in ("游泳", "滑梯", "滑板", "滑墙板", "水上"):
        syns = {"游泳", "滑梯", "冲浪", "滑板", "水上", "玩水", "海"}
    elif key in ("渔民", "收获"):
        # 必须真渔民/码头，禁止普通海景+船顶替
        syns = {"渔民", "渔船", "码头", "收获"}
    elif key in ("青山", "山"):
        syns = {"山", "青山", "风景", "空镜"}
    elif key in ("展会", "讲会", "展位"):
        syns = {"展会", "展台", "展位", "设备", "人群", "科技"}
    elif key in ("人工智能", "AI", "聊天框", "数字世界", "物理世界"):
        syns = {"展会", "展台", "设备", "屏幕", "科技", "AI", "机器"}
    elif key in ("设备", "供应商"):
        syns = {"设备", "展会", "展台", "机器", "科技"}
    elif key in ("客户", "团队", "售后", "信任"):
        syns = {"展会", "商务", "人群", "交谈", "展台"}
    # 去掉「自拍」误命中
    tags_eff = tags - {"自拍", "出镜", "人脸"}
    if tags_eff & syns:
        return True
    for t in tags_eff:
        for s in syns:
            if s and (s in t or t in s):
                return True
    return False


def _strong_keys(keys: list[str]) -> list[str]:
    return [k for k in keys if k in STRONG_SCENIC]


def _score_broll(
    broll: dict,
    keys: list[str],
    aliases: dict[str, list[str]],
    *,
    require_strong_hit: bool = False,
) -> float | None:
    tags = set(broll.get("tags") or [])
    is_selfie = bool(broll.get("face")) or (
        bool(tags & {"自拍", "出镜", "人脸"})
        and not bool(tags & {"河马", "水鸟", "鸟", "空镜", "倒影", "长颈鹿", "斑马"})
    )
    strong = _strong_keys(keys)

    hit_strong = 0
    hit_weak = 0
    score = 0.0

    for k in keys:
        if not _tag_hit(tags, k, aliases):
            continue
        if k in STRONG_SCENIC:
            hit_strong += 1
            if k in ("河马", "海马", "水鸟", "鸟", "长颈鹿", "斑马"):
                score += 6.0
            elif k in ("耳朵", "眼睛", "飞翔", "飞过", "倒影"):
                score += 5.0
            else:
                score += 3.5
        else:
            hit_weak += 1
            score += 1.2

    primary = [k for k in keys if k in PRIMARY_SCENIC]
    hit_primary = sum(1 for k in primary if _tag_hit(tags, k, aliases))

    if require_strong_hit:
        if primary and hit_primary == 0:
            return None
        if not primary and hit_strong == 0:
            return None

    if strong and hit_strong == 0:
        return None  # 强词必须真命中，禁止自拍/泛湖景顶替
    if primary and hit_primary == 0 and require_strong_hit:
        return None

    if is_selfie and hit_strong == 0:
        # 自拍只允许盖「乘船/小船」类
        if not (set(keys) & {"船", "乘船", "小船"}):
            return None
        score -= 1.0

    if not keys:
        score = 0.25
        if tags & {"空镜", "风景", "湖", "湖面", "树"}:
            score += 0.5
        if is_selfie:
            return None
    elif hit_strong == 0 and hit_weak == 0:
        if is_selfie:
            return None
        if tags & {"空镜", "风景", "湖", "湖面", "树", "天空"}:
            score = 0.35
        else:
            return None

    dur = float(broll.get("duration") or 0)
    if 3 <= dur <= 25:
        score += 0.3
    if dur >= 8:
        score += 0.2
    hs = broll.get("highlight_score")
    if isinstance(hs, (int, float)):
        score += max(0.0, min(10.0, float(hs))) * 0.4
    shake = broll.get("shake")
    if isinstance(shake, (int, float)) and float(shake) > 0.45:
        score -= float(shake)
    return score


def _broll_avail(broll: dict, photo_dur: float) -> float:
    if broll.get("kind") == "image":
        return max(photo_dur, 3.0)
    return max(0.4, float(broll.get("duration") or 1.0))


def _broll_window(broll: dict, need: float, photo_dur: float) -> tuple[float, float]:
    if broll.get("kind") == "image":
        return 0.0, need
    avail = _broll_avail(broll, photo_dur)
    take = min(avail, need)
    src_start = min(avail * 0.08, max(0.0, avail - take))
    src_end = min(avail, src_start + take)
    src_start = max(0.0, src_end - take)
    return src_start, src_end


def run_match(
    voiceover: dict[str, Any],
    broll_tags: dict[str, Any],
    work_dir: Path,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    narr = cfg.get("narrative") or {}
    stages = narr.get("stages") or []
    default_face = float(narr.get("default_face_ratio") or 0.12)
    mcfg = cfg.get("match") or {}
    aliases = mcfg.get("keyword_aliases") or {}
    reuse_broll = bool(mcfg.get("reuse_broll", False))
    max_reuse = int(mcfg.get("max_reuse") or 1)
    if not reuse_broll:
        max_reuse = 1
    # 静态图默认永不复用（即使视频允许 max_reuse>1）
    max_reuse_image = int(mcfg.get("max_reuse_image") or 1)
    if max_reuse_image < 1:
        max_reuse_image = 1
    target_face = float(mcfg.get("target_face_ratio") or default_face)
    force_face_open = int(mcfg.get("force_face_open_segments") or 1)
    force_face_close = int(mcfg.get("force_face_close_segments") or 1)

    ai_hints: dict[str, Any] = {}
    hints_path = work_dir / "ai_match_hints.json"
    if hints_path.exists():
        try:
            from ..utils import read_json

            ai_hints = read_json(hints_path) or {}
            print(
                f"  AI hints: force_face={len(ai_hints.get('force_face_texts') or [])} "
                f"prefer_broll={len(ai_hints.get('prefer_broll_texts') or [])} "
                f"scores={len(ai_hints.get('highlight_scores') or {})}"
            )
        except Exception as e:
            print(f"  [warn] ai_match_hints: {e}")
    if isinstance(ai_hints.get("target_face_ratio"), (int, float)):
        target_face = float(ai_hints["target_face_ratio"])
    force_face_texts = list(ai_hints.get("force_face_texts") or [])
    prefer_broll_texts = list(ai_hints.get("prefer_broll_texts") or [])
    hs_map = ai_hints.get("highlight_scores") or {}

    photo_dur = float((cfg.get("broll") or {}).get("photo_duration_sec") or 3.0)
    brolls = list(broll_tags.get("broll") or [])
    for b in brolls:
        if b.get("highlight_score") is None and b.get("name") in hs_map:
            try:
                b["highlight_score"] = float(hs_map[b["name"]])
            except (TypeError, ValueError):
                pass
        # 统一 path key，避免相对/绝对路径算成两份
        try:
            b["path"] = str(Path(b["path"]).resolve())
        except Exception:
            b["path"] = str(b.get("path") or "")
        name = str(b.get("name") or Path(b["path"]).name)
        b["name"] = name
        if b.get("kind") is None:
            ext = Path(name).suffix.lower()
            b["kind"] = "image" if ext in {".jpg", ".jpeg", ".png", ".webp", ".heic"} else "video"

    use_count: dict[str, int] = {}
    vo_timeline = list(voiceover.get("timeline") or [])
    n = len(vo_timeline)

    def _path_key(path: str) -> str:
        try:
            return str(Path(path).resolve())
        except Exception:
            return str(path)

    def _is_image_broll(b: dict) -> bool:
        if b.get("kind") == "image":
            return True
        return Path(str(b.get("name") or b.get("path") or "")).suffix.lower() in {
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".heic",
        }

    def _limit_for(b: dict) -> int:
        return max_reuse_image if _is_image_broll(b) else max_reuse

    def broll_available() -> list[dict]:
        out = []
        for b in brolls:
            key = _path_key(b["path"])
            if use_count.get(key, 0) < _limit_for(b):
                out.append(b)
        return out

    def consume(path: str) -> None:
        key = _path_key(path)
        use_count[key] = use_count.get(key, 0) + 1

    def choose_broll(
        keys: list[str],
        need: float,
        *,
        require_strong: bool = False,
    ) -> dict | None:
        cands = broll_available()
        scored: list[tuple[float, dict]] = []
        for b in cands:
            if _broll_avail(b, photo_dur) + 0.08 < need:
                continue
            sc = _score_broll(
                b, keys, aliases, require_strong_hit=require_strong
            )
            if sc is None:
                continue
            # 轻微偏好尚未用过的静态图（扩大多样性）
            if _is_image_broll(b) and use_count.get(_path_key(b["path"]), 0) == 0:
                sc += 0.15
            scored.append((sc, b))
        if not scored:
            return None
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]

    def make_broll_piece(seg: dict, b: dict, keys: list[str], stage_id: str) -> dict:
        t0 = float(seg["timeline_start"])
        t1 = float(seg["timeline_end"])
        need = t1 - t0
        src_start, _ = _broll_window(b, need, photo_dur)
        return {
            "type": "broll",
            "timeline_start": round(t0, 3),
            "timeline_end": round(t1, 3),
            "duration": round(need, 3),
            "src": b["path"],
            "src_name": b["name"],
            "src_start": round(src_start, 3),
            "src_end": round(src_start + need, 3),
            "needs_freeze": False,
            "tags": b.get("tags") or [],
            "text": seg.get("text") or "",
            "stage": stage_id,
            "match_keys": keys,
            "highlight_score": b.get("highlight_score"),
        }

    def make_talk_piece(seg: dict, keys: list[str], stage_id: str) -> dict:
        return {
            "type": "talk",
            "timeline_start": round(float(seg["timeline_start"]), 3),
            "timeline_end": round(float(seg["timeline_end"]), 3),
            "duration": round(float(seg["duration"]), 3),
            "src": seg["src"],
            "src_name": seg["src_name"],
            "src_start": round(float(seg["src_start"]), 3),
            "src_end": round(float(seg["src_end"]), 3),
            "text": seg.get("text") or "",
            "stage": stage_id,
            "match_keys": keys,
        }

    # 每句预计算
    meta = []
    for i, seg in enumerate(vo_timeline):
        text = (seg.get("text") or "").strip()
        keys = _extract_match_keys(text, aliases)
        if _text_hit(text, prefer_broll_texts):
            for p in prefer_broll_texts:
                if p in text:
                    # 把 prefer 子串也并进 keys 提示
                    for k in _extract_match_keys(p, aliases):
                        if k not in keys:
                            keys.append(k)
        stage_id, stage_face = _stage_for_text(text, stages, default_face)
        strong = _strong_keys(keys)
        meta.append(
            {
                "i": i,
                "seg": seg,
                "text": text,
                "keys": keys,
                "strong": strong,
                "stage_id": stage_id,
                "stage_face": stage_face,
                "dur": float(seg["duration"]),
            }
        )

    # assignments[i] = piece dict or None
    assignments: list[dict | None] = [None] * n

    # -------- Pass 0: 开场/收尾/金句强制露脸（主角必须先出镜）--------
    # 不设时长上限：首句口播经常 >5s，旧逻辑会因此整句被 B-roll 顶掉。
    force_face_idx: set[int] = set()
    open_n = max(0, int(force_face_open))
    close_n = max(0, int(force_face_close))
    for i in range(min(open_n, n)):
        force_face_idx.add(i)
    for i in range(max(0, n - close_n), n):
        force_face_idx.add(i)
    for m in meta:
        if _text_hit(m["text"], force_face_texts):
            force_face_idx.add(m["i"])

    for i in sorted(force_face_idx):
        m = meta[i]
        assignments[i] = make_talk_piece(m["seg"], m["keys"], m["stage_id"])
        print(
            f"  [P0 face] #{i} force talk | {(m['text'] or '')[:32]}"
        )

    # -------- Pass 1: 主体景物句优先（河马/水鸟/倒影/船…）--------
    def _p1_rank(m: dict) -> tuple:
        pk = primary_keys(m["keys"])
        # 河马/水鸟最优先，且按时长降序
        tier = 0
        if any(k in pk for k in ("河马", "海马", "耳朵", "眼睛")):
            tier = 0
        elif any(k in pk for k in ("水鸟", "鸟", "飞翔", "飞过")):
            tier = 1
        elif any(k in pk for k in ("倒影", "船", "乘船", "小船", "日落")):
            tier = 2
        elif any(k in pk for k in ("长颈鹿", "斑马")):
            tier = 3
        else:
            tier = 4
        return (tier, -m["dur"])

    strong_order = sorted(
        [m for m in meta if primary_keys(m["keys"]) or m["strong"]],
        key=_p1_rank,
    )
    for m in strong_order:
        if assignments[m["i"]] is not None:
            continue  # 已强制露脸的句不再被 P1 B-roll 抢走
        pk = primary_keys(m["keys"])
        # 仅有「树」的弱强词不进 P1 抢镜头
        if not pk and m["strong"] == ["树"]:
            continue
        b = choose_broll(m["keys"], m["dur"], require_strong=True)
        if b is None:
            continue
        assignments[m["i"]] = make_broll_piece(
            m["seg"], b, m["keys"], m["stage_id"]
        )
        consume(b["path"])
        print(
            f"  [P1] #{m['i']} keys={pk or m['strong']} → {b['name'][:20]} "
            f"tags={b.get('tags', [])[:4]}"
        )

    # -------- Pass 2: 未分配句按「时长从长到短」贪心盖画，短 B-roll 留给短句 --------
    full_dur = sum(m["dur"] for m in meta) or 1.0
    face_dur = sum(
        float(meta[i]["dur"]) for i in force_face_idx if 0 <= i < n
    )

    pending = [m for m in meta if assignments[m["i"]] is None]
    # 长句优先用长空镜，避免只剩短 B-roll 时被迫整段露脸
    pending.sort(key=lambda m: -m["dur"])

    for m in pending:
        i = m["i"]
        text = m["text"]
        keys = m["keys"]
        strong = m["strong"]
        dur = m["dur"]
        stage_id = m["stage_id"]
        face_share = face_dur / full_dur

        # 默认盖画；开场/收尾/金句已在 P0 强制露脸
        hard_face = False
        pk = primary_keys(keys)

        piece = None
        if not hard_face and broll_available():
            # 主体句：必须标签对齐；非主体：通用空镜
            b = choose_broll(keys, dur, require_strong=bool(pk))
            if b is None and not pk:
                b = choose_broll([], dur, require_strong=False)
            if b is not None:
                piece = make_broll_piece(m["seg"], b, keys, stage_id)
                consume(b["path"])

        if piece is None:
            piece = make_talk_piece(m["seg"], keys, stage_id)
            face_dur += dur
        assignments[i] = piece

    picture_timeline = [a for a in assignments if a is not None]

    # -------- 终检：B-roll / 静态图硬去重（max_reuse / max_reuse_image）--------
    by_path: dict[str, list[int]] = {}
    for idx, p in enumerate(picture_timeline):
        if p.get("type") != "broll":
            continue
        key = _path_key(str(p.get("src") or ""))
        by_path.setdefault(key, []).append(idx)

    broll_by_path = {_path_key(b["path"]): b for b in brolls}
    fixed_reuse = 0
    for key, idxs in list(by_path.items()):
        bmeta = broll_by_path.get(key) or {}
        limit = max_reuse_image if (
            Path(key).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".heic"}
            or bmeta.get("kind") == "image"
        ) else max_reuse
        if len(idxs) <= limit:
            continue
        # 保留前 limit 次，后续强制换素材或改 talk
        for idx in idxs[limit:]:
            p = picture_timeline[idx]
            keys = list(p.get("match_keys") or [])
            need = float(p.get("duration") or 1.0)
            # 临时把当前 path 记满，迫使 choose 换人
            use_count[key] = max(use_count.get(key, 0), limit)
            alt = choose_broll(keys, need, require_strong=bool(primary_keys(keys)))
            if alt is None:
                alt = choose_broll([], need, require_strong=False)
            if alt is not None and _path_key(alt["path"]) != key:
                seg_like = {
                    "timeline_start": p["timeline_start"],
                    "timeline_end": p["timeline_end"],
                    "duration": p["duration"],
                    "text": p.get("text") or "",
                    "src": p.get("src"),
                    "src_name": p.get("src_name"),
                    "src_start": p.get("src_start"),
                    "src_end": p.get("src_end"),
                }
                new_p = make_broll_piece(seg_like, alt, keys, p.get("stage") or "body")
                picture_timeline[idx] = new_p
                consume(alt["path"])
                fixed_reuse += 1
                print(
                    f"  [dedupe] #{idx} reuse {Path(key).name} → {alt['name'][:28]}"
                )
            else:
                # 无替补：改露脸，避免静图/镜头重复
                seg_like = {
                    "timeline_start": p["timeline_start"],
                    "timeline_end": p["timeline_end"],
                    "duration": p["duration"],
                    "text": p.get("text") or "",
                    "src": p.get("src"),
                    "src_name": p.get("src_name"),
                    "src_start": p.get("src_start") or 0,
                    "src_end": p.get("src_end") or p.get("duration") or 1,
                }
                # 尽量从 voiceover 段找回 talk 源
                vo_seg = None
                for vs in vo_timeline:
                    if abs(float(vs.get("timeline_start", -1)) - float(p["timeline_start"])) < 0.05:
                        vo_seg = vs
                        break
                if vo_seg is not None:
                    picture_timeline[idx] = make_talk_piece(
                        vo_seg, keys, p.get("stage") or "body"
                    )
                    fixed_reuse += 1
                    print(f"  [dedupe] #{idx} reuse {Path(key).name} → talk (no alt)")

    if fixed_reuse:
        # 重建 use_count 供 stats
        use_count = {}
        for p in picture_timeline:
            if p.get("type") == "broll":
                consume(str(p.get("src") or ""))

    # 微间隙
    if picture_timeline:
        for idx in range(1, len(picture_timeline)):
            prev, cur = picture_timeline[idx - 1], picture_timeline[idx]
            gap = float(cur["timeline_start"]) - float(prev["timeline_end"])
            if 0.001 < gap < 0.08:
                if prev["type"] == "broll":
                    prev["timeline_end"] = cur["timeline_start"]
                    prev["duration"] = round(
                        float(prev["timeline_end"]) - float(prev["timeline_start"]), 3
                    )
                else:
                    cur["timeline_start"] = prev["timeline_end"]
                    cur["duration"] = round(
                        float(cur["timeline_end"]) - float(cur["timeline_start"]), 3
                    )

    talk_dur = sum(float(p["duration"]) for p in picture_timeline if p["type"] == "talk")
    broll_dur = sum(float(p["duration"]) for p in picture_timeline if p["type"] == "broll")
    all_dur = max(talk_dur + broll_dur, 0.01)

    print("  semantic check (primary keys):")
    for p in picture_timeline:
        pk = [k for k in (p.get("match_keys") or []) if k in PRIMARY_SCENIC]
        if not pk:
            continue
        tags = set(p.get("tags") or [])
        if p["type"] == "talk":
            print(f"    ⚠ FACE while keys={pk} | {(p.get('text') or '')[:28]}")
        else:
            ok = any(_tag_hit(tags, k, aliases) for k in pk)
            mark = "✓" if ok else "✗"
            print(
                f"    {mark} B-roll keys={pk} tags={list(tags)[:5]} | "
                f"{(p.get('text') or '')[:28]}"
            )

    result = {
        "total_duration": voiceover.get("total_duration") or 0,
        "slot_count": len(picture_timeline),
        "picture_count": len(picture_timeline),
        "picture_timeline": picture_timeline,
        "broll_usage": {Path(k).name: v for k, v in use_count.items()},
        "reuse_broll": reuse_broll,
        "max_reuse": max_reuse,
        "max_reuse_image": max_reuse_image,
        "dedupe_fixed": fixed_reuse,
        "broll_pool": len(brolls),
        "broll_used": len(use_count),
        "broll_left": len(brolls) - len(use_count),
        "stats": {
            "talk_segments": sum(1 for p in picture_timeline if p["type"] == "talk"),
            "broll_segments": sum(1 for p in picture_timeline if p["type"] == "broll"),
            "talk_duration": round(talk_dur, 2),
            "broll_duration": round(broll_dur, 2),
            "face_ratio_actual": round(talk_dur / all_dur, 3),
            "target_face_ratio": target_face,
        },
    }
    write_json(work_dir / "picture_plan.json", result)
    st = result["stats"]
    print(
        f"  picture plan: {st['talk_segments']} talk + {st['broll_segments']} broll | "
        f"face {st['face_ratio_actual']:.0%} (target {target_face:.0%}) | "
        f"broll {st['broll_duration']:.0f}s / talk {st['talk_duration']:.0f}s | "
        f"files {len(use_count)}/{len(brolls)}"
    )
    return result


def primary_keys(keys: list[str]) -> list[str]:
    return [k for k in keys if k in PRIMARY_SCENIC]
