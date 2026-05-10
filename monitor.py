# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "requests==2.32.3",
#   "beautifulsoup4==4.12.3",
# ]
# ///
"""
LG 트윈스 마킹키트 품절 모니터링
- 재입고 / 품절 변경 시 Discord 알림 발송
- GitHub Actions에서 10분마다 실행
"""

import requests
from bs4 import BeautifulSoup
import json
import os
from datetime import datetime


# ── 설정 ──────────────────────────────────────────────
URL = (
    "https://twinslockerdium.co.kr/product"
    "/%EA%B0%9C%EB%B3%84%ED%8C%90%EB%A7%A4%EC%9A%A9lg%ED%8A%B8%EC%9C%88%EC%8A%A4"
    "-25%EB%85%84-%EC%9E%90%EC%88%98-%EB%A7%88%ED%82%B9%ED%82%A4%ED%8A%B8%EC%9B%90%EC%A0%95"
    "/203/category/144/display/1/"
)

HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "ko,en-US;q=0.9,en;q=0.8",
    "cache-control": "no-cache",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
    ),
    "referer": "https://twinslockerdium.co.kr/product/list.html?cate_no=144",
}

LOG_FILE = "status.json"

# GitHub Actions Secret에서 주입
# 로컬 실행
# from dotenv import load_dotenv
# load_dotenv()
# DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
# GitHub Actions
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# ── Discord 알림 ───────────────────────────────────────

def send_discord(changes: list[str], curr: list[dict]):
    if not DISCORD_WEBHOOK_URL:
        print("  [Discord] 웹훅 URL 없음 - 스킵")
        return
 
    # 변경사항 embed 색상
    has_restock = any("✅" in c for c in changes)
    has_soldout = any("❌" in c for c in changes)
    color = (
        0x00c851 if has_restock and not has_soldout else
        0xff4444 if has_soldout and not has_restock else
        0xff8800
    )
 
    # 전체 현황 텍스트 구성
    available = [o for o in curr if not o["sold_out"]]
    sold_out  = [o for o in curr if o["sold_out"]]
 
    available_text = "- " + "\n- ".join(o["name"] for o in available) or "없음"
    sold_out_text  = "- " + "\n- ".join(o["name"] for o in sold_out)  or "없음"
 
    embeds = [
        # ① 변경사항
        {
            "title": "🔔 변경사항",
            "description": "\n".join(changes),
            "color": color,
            "url": URL,
        },
        # ② 전체 현황
        {
            "title": f"📋 전체 현황  ({len(available)}/{len(curr)}명 구매가능)",
            "color": 0x5865f2,  # Discord 블루
            "fields": [
                {
                    "name": f"✅ 구매가능 ({len(available)}명)",
                    "value": available_text,
                    "inline": False,
                },
                {
                    "name": f"❌ 품절 ({len(sold_out)}명)",
                    "value": sold_out_text,
                    "inline": False,
                },
            ],
            "footer": {
                "text": f"확인 시각: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
            },
        },
    ]
 
    try:
        resp = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"embeds": embeds},
            timeout=10,
        )
        if resp.status_code in (200, 204):
            print("  [Discord] 전송 성공 ✅")
        else:
            print(f"  [Discord] 전송 실패: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"  [Discord] 오류: {e}")


# ── 크롤링 ────────────────────────────────────────────

def fetch_options() -> list[dict]:
    resp = requests.get(URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    select = soup.find("select", {"name": "option1"})
    if not select:
        raise ValueError("옵션 select 태그를 찾을 수 없습니다.")

    options = []
    for opt in select.find_all("option"):
        value = opt.get("value", "")
        if value in ("*", "**") or not value:
            continue
        label    = opt.get_text(strip=True)
        sold_out = "[품절]" in label
        name     = label.replace("[품절]", "").strip()
        options.append({"value": value, "name": name, "sold_out": sold_out})
    return options


def load_previous() -> list[dict] | None:
    if not os.path.exists(LOG_FILE):
        return None
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("options")
    except (json.JSONDecodeError, KeyError):
        return None


def save_current(options: list[dict]):
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {"last_checked": datetime.utcnow().isoformat() + "Z", "options": options},
            f, ensure_ascii=False, indent=2,
        )


def detect_changes(prev: list[dict], curr: list[dict]) -> list[str]:
    prev_map = {o["value"]: o for o in prev}
    curr_map = {o["value"]: o for o in curr}
    messages = []

    for v, c in curr_map.items():
        p = prev_map.get(v)
        if p is None:
            tag = " [품절]" if c["sold_out"] else " [구매가능]"
            messages.append(f"🆕 새 옵션: {c['name']}{tag}")
        elif p["sold_out"] and not c["sold_out"]:
            messages.append(f"✅ 재입고!!  {c['name']}")
        elif not p["sold_out"] and c["sold_out"]:
            messages.append(f"❌ 품절됨:  {c['name']}")

    for v, p in prev_map.items():
        if v not in curr_map:
            messages.append(f"🗑️  옵션 삭제: {p['name']}")

    return messages


# ── 메인 ──────────────────────────────────────────────

def main():
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"\n{'='*55}")
    print(f"[{now}] 모니터링 실행")

    try:
        curr = fetch_options()
    except Exception as e:
        print(f"❌ 요청 실패: {e}")
        raise SystemExit(1)

    available = [o for o in curr if not o["sold_out"]]
    sold_out  = [o for o in curr if o["sold_out"]]
    print(f"전체: {len(curr)}명 | 구매가능: {len(available)}명 | 품절: {len(sold_out)}명")
    print(f"구매가능: {', '.join(o['name'] for o in available)}")
    if sold_out:
        print(f"품절:     {', '.join(o['name'] for o in sold_out)}")

    prev = load_previous()
    if prev:
        changes = detect_changes(prev, curr)
        if changes:
            print("\n🔔 변경사항 감지! Discord 알림 전송 중...")
            for msg in changes:
                print(f"   {msg}")
            send_discord(changes, curr)
 
            summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "")
            if summary_path:
                with open(summary_path, "a", encoding="utf-8") as f:
                    f.write("## 🔔 트윈스 마킹키트 변경사항\n")
                    for msg in changes:
                        f.write(f"- {msg}\n")
        else:
            print("변경사항 없음")
    else:
        print("첫 실행 - 기준 상태 저장")
        send_discord([], curr)

    save_current(curr)
    print(f"{'='*55}\n")


if __name__ == "__main__":
    main()
