"""
每日文献摘要生成脚本。
流程：抓取RSS -> 关键词/主题粗筛 -> 尝试补全作者单位(Crossref) -> Claude打分与摘要 -> 输出JSON。
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
from anthropic import Anthropic

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "docs" / "data"
SEEN_PATH = DATA_DIR / "seen.json"
CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+")

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


def fetch_candidates(config):
    max_age = timedelta(days=config.get("max_age_days", 7))
    cutoff = datetime.now(timezone.utc) - max_age
    keywords = [k.lower() for k in config["keywords"]]
    broad_tags = {"ecology", "climate", "environmental science", "carbon", "biodiversity"}

    seen = load_seen()
    candidates = []

    for feed_cfg in config["feeds"]:
        try:
            resp = requests.get(feed_cfg["url"], headers=HTTP_HEADERS, timeout=15)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
        except requests.RequestException as e:
            print(f"[warn] 无法获取 {feed_cfg['name']} 的RSS: {e}", file=sys.stderr)
            continue

        if parsed.bozo and not parsed.entries:
            print(f"[warn] 无法解析 {feed_cfg['name']} 的RSS: {parsed.get('bozo_exception')}", file=sys.stderr)
            continue

        for entry in parsed.entries:
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


def fetch_affiliations(doi):
    if not doi:
        return None
    try:
        resp = requests.get(f"https://api.crossref.org/works/{doi}", timeout=8)
        if resp.status_code != 200:
            return None
        authors = resp.json().get("message", {}).get("author", [])
        affiliations = []
        for a in authors:
            for aff in a.get("affiliation", []):
                name = aff.get("name")
                if name and name not in affiliations:
                    affiliations.append(name)
        return "; ".join(affiliations[:3]) if affiliations else None
    except (requests.RequestException, ValueError):
        return None


def enrich_with_affiliations(candidates):
    for c in candidates:
        c["affiliation"] = fetch_affiliations(c.get("doi"))
        time.sleep(0.2)  # 避免过快请求Crossref
    return candidates


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
            resp = client.messages.create(
                model=model,
                max_tokens=4000,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            text = resp.content[0].text.strip()
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
        print("[error] 所有打分批次均失败，请检查 ANTHROPIC_API_KEY 是否正确、账户额度是否充足、模型名是否可用。", file=sys.stderr)
        sys.exit(1)

    return results, processed_ids


def main():
    config = load_config()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[error] 未设置 ANTHROPIC_API_KEY 环境变量", file=sys.stderr)
        sys.exit(1)

    print("[info] 抓取RSS并粗筛...")
    candidates = fetch_candidates(config)
    print(f"[info] 粗筛后候选文章数: {len(candidates)}")

    if candidates:
        print("[info] 尝试补全作者单位信息 (Crossref)...")
        candidates = enrich_with_affiliations(candidates)

        print("[info] 调用Claude进行打分与摘要...")
        client = Anthropic(api_key=api_key)
        scored, processed_ids = score_and_summarize(
            client, config["model"], config["research_profile"], candidates
        )
    else:
        scored, processed_ids = [], set()

    threshold = config.get("relevance_threshold", 6)
    digest = [item for item in scored if item.get("relevance_score", 0) >= threshold]
    digest.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)

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
