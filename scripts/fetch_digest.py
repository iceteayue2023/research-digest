"""
每日文献摘要生成脚本。
流程：抓取RSS -> 关键词/主题粗筛 -> 尝试补全作者单位(Crossref) -> DeepSeek打分与摘要 -> 输出JSON。
由 GitHub Actions 每日定时调用，输出写入 docs/data/。
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests
import yaml
from dateutil import parser as dateparser
from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "docs" / "data"
SEEN_PATH = DATA_DIR / "seen.json"
CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>?]+")

# 不带 User-Agent 的请求会被 Nature/Wiley 等站点的反爬机制拦截，
# 返回的错误页不是合法XML，导致 feedparser 解析失败。
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    )
}


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_seen():
    if SEEN_PATH.exists():
        with open(SEEN_PATH, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen_ids):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(seen_ids), f, ensure_ascii=False, indent=2)


def strip_html(raw):
    if not raw:
        return ""
    return re.sub("<[^<]+?>", "", raw).strip()


def extract_doi(entry):
    for key in ("prism_doi", "dc_identifier", "id", "link"):
        val = entry.get(key)
        if val:
            m = DOI_RE.search(val)
            if m:
                return m.group(0).rstrip(".")
    summary = entry.get("summary", "")
    m = DOI_RE.search(summary)
    return m.group(0).rstrip(".") if m else None


JINA_ENTRY_RE = re.compile(
    r"### \[(?P<title>.*?)\]\((?P<link>.*?)\)\n\n(?P<meta>.*?)\n\n\[.*?\]\(.*?\)\n\n"
    r"(?P<date>[A-Za-z]{3}, \d{1,2} \w+ \d{4}[^\n]*)",
    re.DOTALL,
)


def fetch_via_jina_proxy(url):
    """一些出版商(Wiley/Science等)会拦截GitHub Actions这类数据中心IP的直连请求(403)。
    退而用 r.jina.ai 这个只读代理转发抓取，它返回的是转成Markdown的正文而非原始XML，
    所以这里用正则单独解析出条目，而不是走 feedparser。"""
    try:
        resp = requests.get(f"https://r.jina.ai/{url}", headers=HTTP_HEADERS, timeout=25)
        if resp.status_code != 200:
            return []
        entries = []
        for m in JINA_ENTRY_RE.finditer(resp.text):
            entries.append({
                "id": m.group("link").strip(),
                "title": m.group("title").strip(),
                "link": m.group("link").strip(),
                "summary": m.group("meta").strip(),
                "published": m.group("date").strip(),
                "tags": [],
            })
        return entries
    except requests.RequestException:
        return []


OG_IMAGE_RE = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\'](.*?)["\']', re.IGNORECASE
)


def extract_og_image(html):
    m = OG_IMAGE_RE.search(html)
    return m.group(1) if m else None


def fetch_og_image(article_url):
    """尝试从文章原始页面抓取 og:image (社交分享预览图/图表首图)，只取图片地址做"引用式"展示，
    不下载不转存图片文件本身。抓不到就返回None，前端会优雅地不显示图片区域。"""
    if not article_url:
        return None
    try:
        resp = requests.get(article_url, headers=HTTP_HEADERS, timeout=12)
        if resp.status_code == 200:
            image = extract_og_image(resp.text)
            if image:
                return image
    except requests.RequestException:
        pass

    try:
        resp = requests.get(
            f"https://r.jina.ai/{article_url}",
            headers={**HTTP_HEADERS, "X-Return-Format": "html"},
            timeout=20,
        )
        if resp.status_code == 200:
            return extract_og_image(resp.text)
    except requests.RequestException:
        pass
    return None


def enrich_with_images(digest_articles):
    for a in digest_articles:
        a["image_url"] = fetch_og_image(a.get("link"))
        time.sleep(0.15)
    return digest_articles


def fetch_feed_entries(feed_cfg):
    try:
        resp = requests.get(feed_cfg["url"], headers=HTTP_HEADERS, timeout=15)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
        if not (parsed.bozo and not parsed.entries):
            return parsed.entries
        print(f"[warn] 无法解析 {feed_cfg['name']} 的RSS: {parsed.get('bozo_exception')}，尝试用代理重试", file=sys.stderr)
    except requests.RequestException as e:
        print(f"[warn] 无法获取 {feed_cfg['name']} 的RSS: {e}，尝试用代理重试", file=sys.stderr)

    proxied = fetch_via_jina_proxy(feed_cfg["url"])
    if not proxied:
        print(f"[warn] 代理重试也失败，跳过 {feed_cfg['name']}", file=sys.stderr)
    return proxied


def fetch_candidates(config):
    max_age = timedelta(days=config.get("max_age_days", 7))
    cutoff = datetime.now(timezone.utc) - max_age
    keywords = [k.lower() for k in config["keywords"]]
    broad_tags = {"ecology", "climate", "environmental science", "carbon", "biodiversity"}

    seen = load_seen()
    candidates = []

    for feed_cfg in config["feeds"]:
        entries = fetch_feed_entries(feed_cfg)

        for entry in entries:
            entry_id = entry.get("id") or entry.get("link")
            if not entry_id or entry_id in seen:
                continue

            published = entry.get("published") or entry.get("updated")
            try:
                pub_dt = dateparser.parse(published) if published else None
                if pub_dt and pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                pub_dt = None
            if pub_dt and pub_dt < cutoff:
                continue

            title = entry.get("title", "")
            summary = strip_html(entry.get("summary", ""))
            tags = " ".join(t.get("term", "") for t in entry.get("tags", []) if t.get("term"))
            haystack = f"{title} {summary} {tags}".lower()

            keyword_hit = any(k in haystack for k in keywords)
            tag_hit = any(bt in haystack for bt in broad_tags)
            if not (keyword_hit or tag_hit):
                continue

            candidates.append({
                "id": entry_id,
                "title": title,
                "link": entry.get("link", ""),
                "summary": summary[:1500],
                "journal": feed_cfg["name"],
                "published": pub_dt.isoformat() if pub_dt else None,
                "doi": extract_doi(entry),
            })

    return candidates


BSKY_HEADERS = {"User-Agent": "research-digest-app (mailto:research-digest@example.com)"}


def fetch_bluesky_posts(config):
    """抓取关注的期刊/学会在 Bluesky 上的官方账号动态，按关键词粗筛，免费公开API，不需要登录。"""
    handles = config.get("bluesky_handles", [])
    if not handles:
        return []

    max_age = timedelta(days=config.get("social_max_age_days", 3))
    cutoff = datetime.now(timezone.utc) - max_age
    keywords = [k.lower() for k in config["keywords"]]
    broad_tags = {"ecology", "climate", "environmental science", "carbon", "biodiversity"}

    posts = []
    for handle in handles:
        try:
            resp = requests.get(
                "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed",
                params={"actor": handle, "limit": 25},
                headers=BSKY_HEADERS,
                timeout=15,
            )
            if resp.status_code != 200:
                print(f"[warn] 无法获取 Bluesky @{handle} 的动态: {resp.status_code}", file=sys.stderr)
                continue
            feed = resp.json().get("feed", [])
        except requests.RequestException as e:
            print(f"[warn] 无法获取 Bluesky @{handle} 的动态: {e}", file=sys.stderr)
            continue

        for item in feed:
            post = item.get("post", {})
            record = post.get("record", {})
            created_at = record.get("createdAt")
            try:
                created_dt = dateparser.parse(created_at) if created_at else None
            except (ValueError, TypeError):
                created_dt = None
            if created_dt and created_dt < cutoff:
                continue

            text = record.get("text", "")
            external = (post.get("embed") or {}).get("external") or {}
            haystack = f"{text} {external.get('title', '')} {external.get('description', '')}".lower()
            if not (any(k in haystack for k in keywords) or any(t in haystack for t in broad_tags)):
                continue

            uri = post.get("uri", "")
            rkey = uri.rsplit("/", 1)[-1] if uri else ""
            author = post.get("author", {})

            posts.append({
                "handle": handle,
                "author_name": author.get("displayName") or handle,
                "text": text,
                "created_at": created_at,
                "post_link": f"https://bsky.app/profile/{handle}/post/{rkey}" if rkey else "",
                "external_title": external.get("title"),
                "external_description": external.get("description"),
                "external_uri": external.get("uri"),
                "external_thumb": external.get("thumb"),
            })

    posts.sort(key=lambda p: p.get("created_at") or "", reverse=True)
    return posts


def fetch_crossref_authors(doi):
    """返回Crossref上的原始作者列表(含ORCID)，供单位提取和作者简介功能复用，只查一次。"""
    if not doi:
        return []
    try:
        resp = requests.get(f"https://api.crossref.org/works/{doi}", timeout=8)
        if resp.status_code != 200:
            return []
        authors = resp.json().get("message", {}).get("author", [])
        return [
            {
                "name": f"{a.get('given', '')} {a.get('family', '')}".strip(),
                "orcid": (a.get("ORCID") or "").replace("http://orcid.org/", "").replace("https://orcid.org/", "") or None,
                "sequence": a.get("sequence"),
                "affiliations": [aff.get("name") for aff in a.get("affiliation", []) if aff.get("name")],
            }
            for a in authors
        ]
    except (requests.RequestException, ValueError):
        return []


def affiliations_from_authors(authors_meta):
    affiliations = []
    for a in authors_meta:
        for name in a.get("affiliations", []):
            if name not in affiliations:
                affiliations.append(name)
    return "; ".join(affiliations[:3]) if affiliations else None


def enrich_with_affiliations(candidates):
    for c in candidates:
        authors_meta = fetch_crossref_authors(c.get("doi"))
        c["authors_meta"] = authors_meta
        c["affiliation"] = affiliations_from_authors(authors_meta)
        time.sleep(0.2)  # 避免过快请求Crossref
    return candidates


OPENALEX_HEADERS = {"User-Agent": "research-digest-app (mailto:research-digest@example.com)"}


def fetch_related_papers(title, exclude_doi=None, limit=4):
    """基于标题在 OpenAlex 上做文本检索找相关文献。
    刚发表的新文章通常还没被引用图谱收录，所以用文本检索而不是引用推荐，
    这样即使文章本身还没被索引也能找到同主题的相关文献。"""
    if not title:
        return []
    try:
        resp = requests.get(
            "https://api.openalex.org/works",
            params={
                "search": title,
                "per_page": limit + 1,
                "select": "title,doi,publication_year,primary_location",
            },
            headers=OPENALEX_HEADERS,
            timeout=10,
        )
        if resp.status_code != 200:
            return []
        results = []
        for w in resp.json().get("results", []):
            w_title = w.get("title") or ""
            if not w_title or w_title.strip().lower() == title.strip().lower():
                continue
            doi = w.get("doi")
            if exclude_doi and doi and exclude_doi.lower() in doi.lower():
                continue
            venue = ((w.get("primary_location") or {}).get("source") or {}).get("display_name")
            results.append({
                "title": w_title,
                "link": doi or "",
                "venue": venue,
                "year": w.get("publication_year"),
            })
            if len(results) >= limit:
                break
        return results
    except requests.RequestException:
        return []


ORCID_HEADERS = {"Accept": "application/json", "User-Agent": "research-digest-app (mailto:research-digest@example.com)"}


def pick_profiled_author(authors_meta):
    """优先选有ORCID的第一作者，其次末位作者(常见的通讯作者位置)，再次任意一个有ORCID的作者。
    没有ORCID的一律不猜，避免同名混淆。"""
    if not authors_meta:
        return None
    if authors_meta[0].get("orcid"):
        return authors_meta[0]
    if authors_meta[-1].get("orcid"):
        return authors_meta[-1]
    return next((a for a in authors_meta if a.get("orcid")), None)


def fetch_orcid_profile(orcid, limit=6):
    try:
        resp = requests.get(f"https://pub.orcid.org/v3.0/{orcid}/works", headers=ORCID_HEADERS, timeout=10)
        if resp.status_code != 200:
            return None
        groups = resp.json().get("group", [])
        works = []
        for g in groups:
            summ = (g.get("work-summary") or [{}])[0]
            title = ((summ.get("title") or {}).get("title") or {}).get("value")
            if not title:
                continue
            year = ((summ.get("publication-date") or {}).get("year") or {}).get("value")
            journal = (summ.get("journal-title") or {}).get("value")
            doi = None
            for eid in (summ.get("external-ids") or {}).get("external-id", []):
                if eid.get("external-id-type") == "doi":
                    doi = eid.get("external-id-value")
                    break
            works.append({"title": title, "year": year, "journal": journal, "doi": doi})

        resp2 = requests.get(f"https://pub.orcid.org/v3.0/{orcid}/employments", headers=ORCID_HEADERS, timeout=10)
        institution = None
        if resp2.status_code == 200:
            groups2 = resp2.json().get("affiliation-group", [])
            if groups2:
                summ2 = (groups2[0].get("summaries") or [{}])[0].get("employment-summary", {})
                institution = (summ2.get("organization") or {}).get("name")

        works.sort(key=lambda w: w.get("year") or "0", reverse=True)
        return {"institution": institution, "works": works[:limit]}
    except (requests.RequestException, ValueError):
        return None


def enrich_with_author_profiles(digest_articles):
    for a in digest_articles:
        author = pick_profiled_author(a.get("authors_meta") or [])
        if not author:
            a["author_profile_raw"] = None
            continue
        profile = fetch_orcid_profile(author["orcid"])
        if profile:
            a["author_profile_raw"] = {"name": author["name"], **profile}
        else:
            a["author_profile_raw"] = None
        time.sleep(0.15)
    return digest_articles


def enrich_with_related_papers(digest_articles):
    for a in digest_articles:
        a["related_papers"] = fetch_related_papers(a["title"], exclude_doi=a.get("doi"))
        time.sleep(0.15)
    return digest_articles


def score_and_summarize(client, model, research_profile, candidates, batch_size=12):
    results = []
    processed_ids = set()
    total_batches = 0
    failed_batches = 0
    for i in range(0, len(candidates), batch_size):
        total_batches += 1
        batch = candidates[i:i + batch_size]
        payload = [
            {
                "id": c["id"],
                "title": c["title"],
                "journal": c["journal"],
                "summary": c["summary"],
                "affiliation": c.get("affiliation") or "未知",
            }
            for c in batch
        ]

        system_prompt = (
            "你是一名科研文献助理，帮助用户从期刊目录中筛选与其研究方向相关的文章。\n"
            f"用户的研究方向：{research_profile}\n\n"
            "对给定的每篇文章，输出一个JSON对象，字段为：\n"
            "id (原样返回), title_zh (标题的中文翻译，简洁准确), "
            "relevance_score (0-10整数，10表示高度相关), "
            "key_conclusion (用中文1-3句话概括文章的核心结论/发现，基于摘要，不要编造摘要中没有的数据), "
            "relevance_note (用中文1-2句话说明这篇文章与用户研究方向的具体关联，如果不相关就说明原因)。\n"
            "只返回一个JSON数组，不要有其他文字、不要用markdown代码块包裹。"
        )
        user_prompt = json.dumps(payload, ensure_ascii=False)

        try:
            resp = client.chat.completions.create(
                model=model,
                max_tokens=4000,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            text = resp.choices[0].message.content.strip()
            text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
            scored = json.loads(text)
        except Exception as e:
            failed_batches += 1
            detail = getattr(e, "message", None) or getattr(e, "body", None) or str(e)
            print(f"[error] 批次 {i} 打分失败 ({type(e).__name__}): {detail}", file=sys.stderr)
            continue

        by_id = {c["id"]: c for c in batch}
        for item in scored:
            src = by_id.get(item.get("id"))
            if not src:
                continue
            processed_ids.add(src["id"])
            results.append({
                **src,
                "title_zh": item.get("title_zh", ""),
                "relevance_score": item.get("relevance_score", 0),
                "key_conclusion": item.get("key_conclusion", ""),
                "relevance_note": item.get("relevance_note", ""),
            })

    if total_batches > 0 and failed_batches == total_batches:
        print("[error] 所有打分批次均失败，请检查 DEEPSEEK_API_KEY 是否正确、账户额度是否充足、模型名是否可用。", file=sys.stderr)
        sys.exit(1)

    return results, processed_ids


def deep_analyze(client, model, research_profile, digest_articles, batch_size=4):
    """对已入选的文章做更深入的中文解读，仅对通过相关性筛选的文章调用，控制成本。"""
    by_id = {a["id"]: a for a in digest_articles}

    for i in range(0, len(digest_articles), batch_size):
        batch = digest_articles[i:i + batch_size]
        payload = []
        for a in batch:
            raw = a.get("author_profile_raw")
            item = {
                "id": a["id"],
                "title": a["title"],
                "journal": a["journal"],
                "summary": a["summary"],
                "key_conclusion": a.get("key_conclusion", ""),
                "author_name": raw["name"] if raw else None,
                "author_institution": (raw or {}).get("institution"),
                "author_other_works": [w["title"] for w in (raw or {}).get("works", [])][:6] if raw else [],
            }
            payload.append(item)

        system_prompt = (
            "你是一名资深科研文献分析师，为一位研究方向是「" + research_profile + "」的科研人员精读文章。\n"
            "你只能看到标题和期刊目录页的简短摘要（可能不完整），如果信息不足以支撑判断，"
            "必须明确说明'基于现有信息推测/信息不足，建议查看原文'，不要编造摘要中没有的具体数据或结论。\n\n"
            "对给定的每篇文章，输出一个JSON对象，字段为：\n"
            "id (原样返回),\n"
            "scientific_question (这篇文章试图回答的核心科学问题，1-2句中文),\n"
            "contributions_limitations (主要贡献和可能的局限性，2-4句中文，若摘要信息不足需说明是推测),\n"
            "follow_up_research (基于这篇文章，可能有价值的后续研究方向，2-3句中文),\n"
            "next_step_perspective (这篇文章能为用户的研究提供什么具体视角、方法启发或研究空白提示，2-3句中文，要具体、避免空泛),\n"
            "why_this_journal (结合期刊定位和摘要内容，推测这篇文章为何能发表在该期刊，比如新颖性/跨学科意义/方法严谨性，1-2句中文，注明是推测),\n"
            "author_profile (仅当提供了 author_name 时才填写：结合 author_name/author_institution/author_other_works，"
            "用2-3句中文介绍这位作者目前所在机构、从其历史论文标题看出的主要研究方向，并指出这些研究与用户方向的关联。"
            "如果没有提供 author_name，此字段返回空字符串，不要编造作者信息)。\n"
            "只返回一个JSON数组，不要有其他文字、不要用markdown代码块包裹。"
        )
        user_prompt = json.dumps(payload, ensure_ascii=False)

        try:
            resp = client.chat.completions.create(
                model=model,
                max_tokens=6000,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            text = resp.choices[0].message.content.strip()
            text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
            analyzed = json.loads(text)
        except Exception as e:
            detail = getattr(e, "message", None) or getattr(e, "body", None) or str(e)
            print(f"[error] 深度解读批次 {i} 失败 ({type(e).__name__}): {detail}", file=sys.stderr)
            continue

        for item in analyzed:
            target = by_id.get(item.get("id"))
            if not target:
                continue
            target["scientific_question"] = item.get("scientific_question", "")
            target["contributions_limitations"] = item.get("contributions_limitations", "")
            target["follow_up_research"] = item.get("follow_up_research", "")
            target["next_step_perspective"] = item.get("next_step_perspective", "")
            target["why_this_journal"] = item.get("why_this_journal", "")

            raw = target.get("author_profile_raw")
            if raw and item.get("author_profile"):
                target["author_profile"] = {
                    "name": raw["name"],
                    "institution": raw.get("institution"),
                    "intro": item.get("author_profile", ""),
                    "other_works": raw.get("works", [])[:5],
                }
            else:
                target["author_profile"] = None

    return digest_articles


def main():
    config = load_config()
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("[error] 未设置 DEEPSEEK_API_KEY 环境变量", file=sys.stderr)
        sys.exit(1)

    print("[info] 抓取RSS并粗筛...")
    candidates = fetch_candidates(config)
    print(f"[info] 粗筛后候选文章数: {len(candidates)}")

    print("[info] 抓取期刊Bluesky动态...")
    social_posts = fetch_bluesky_posts(config)
    print(f"[info] 匹配到 {len(social_posts)} 条Bluesky动态")

    if candidates:
        print("[info] 尝试补全作者单位信息 (Crossref)...")
        candidates = enrich_with_affiliations(candidates)

        print("[info] 调用DeepSeek进行打分与摘要...")
        client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
        scored, processed_ids = score_and_summarize(
            client, config["model"], config["research_profile"], candidates
        )
    else:
        scored, processed_ids = [], set()

    threshold = config.get("relevance_threshold", 6)
    digest = [item for item in scored if item.get("relevance_score", 0) >= threshold]
    digest.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)

    if digest:
        print("[info] 抓取作者ORCID履历、相关文献与预览图...")
        digest = enrich_with_author_profiles(digest)
        digest = enrich_with_related_papers(digest)
        digest = enrich_with_images(digest)

        print(f"[info] 对 {len(digest)} 篇入选文章生成深度解读 (DeepSeek)...")
        digest = deep_analyze(client, config["model"], config["research_profile"], digest)

        for a in digest:
            a.pop("author_profile_raw", None)
            a.pop("authors_meta", None)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output = {
        "date": today,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(digest),
        "articles": digest,
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DATA_DIR / f"{today}.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    with open(DATA_DIR / "latest.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    social_output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(social_posts),
        "posts": social_posts,
    }
    with open(DATA_DIR / "social_latest.json", "w", encoding="utf-8") as f:
        json.dump(social_output, f, ensure_ascii=False, indent=2)

    # 更新已处理索引（更新到 index.json 供前端展示历史日期列表）
    index_path = DATA_DIR / "index.json"
    dates = []
    if index_path.exists():
        with open(index_path, "r", encoding="utf-8") as f:
            dates = json.load(f)
    if today not in dates:
        dates.append(today)
    dates = sorted(set(dates), reverse=True)[:60]
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(dates, f, ensure_ascii=False, indent=2)

    seen = load_seen()
    seen.update(processed_ids)
    save_seen(seen)

    print(f"[info] 完成，今日推送 {len(digest)} 篇文章。")


if __name__ == "__main__":
    main()
