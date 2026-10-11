import requests
import hashlib
import re
import time
import xml.etree.ElementTree as ET

from pathlib import Path
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime, parsedate_to_datetime


SOURCE_URL = "https://fukuyama.hosp.go.jp/news.html"
OUTPUT = Path(__file__).parent / "fukuyama_mc.xml"

JST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    )
}

MAX_RETRIES = 3

DATE_RE = re.compile(
    r"(20\d{2})年\s*0?(\d{1,2})月\s*0?(\d{1,2})日"
)


# --------------------------------------------------
# HTTP取得
# --------------------------------------------------

def fetch(url):

    for attempt in range(1, MAX_RETRIES + 1):

        try:

            print(
                f"取得 {attempt}/{MAX_RETRIES}"
            )

            response = requests.get(
                url,
                headers=HEADERS,
                timeout=30
            )

            print(
                "HTTP:",
                response.status_code
            )

            response.raise_for_status()

            return response

        except requests.RequestException as e:

            print(
                "取得失敗:",
                e
            )

            if attempt < MAX_RETRIES:

                print(
                    "10秒待って再試行します。"
                )

                time.sleep(10)

    return None


# --------------------------------------------------
# 既存RSS読み込み
# --------------------------------------------------

old_items = {}

if OUTPUT.exists():

    try:

        tree = ET.parse(
            OUTPUT
        )

        for item in tree.getroot().findall(
            "./channel/item"
        ):

            guid = item.findtext(
                "guid",
                ""
            )

            if not guid:
                continue

            old_items[guid] = {
                "title": item.findtext(
                    "title",
                    ""
                ),
                "link": item.findtext(
                    "link",
                    ""
                ),
                "description": item.findtext(
                    "description",
                    ""
                ),
                "pubDate": item.findtext(
                    "pubDate",
                    ""
                ),
                "guid": guid,
            }

    except Exception as e:

        print(
            "既存RSS読み込みエラー:",
            e
        )

        print(
            "安全のため今回は更新しません。"
        )

        raise SystemExit(0)


print(
    "既存RSS件数:",
    len(old_items)
)


# --------------------------------------------------
# ページ取得
# --------------------------------------------------

response = fetch(
    SOURCE_URL
)

if response is None:

    print(
        "サイトを取得できないため、"
        "既存XMLを維持します。"
    )

    raise SystemExit(0)


soup = BeautifulSoup(
    response.content,
    "html.parser"
)


# --------------------------------------------------
# 「お知らせ」タブだけ取得
# --------------------------------------------------

notice = soup.select_one(
    "div#notice"
)

if notice is None:

    print(
        "「お知らせ」領域 div#notice が"
        "見つかりません。"
    )

    print(
        "サイト構造変更の可能性があるため、"
        "既存XMLを維持します。"
    )

    raise SystemExit(0)


blocks = notice.select(
    "div.item"
)

print(
    "お知らせ候補:",
    len(blocks),
    "件"
)


if len(blocks) < 10:

    print(
        "候補件数が異常に少ないため、"
        "既存XMLを維持します。"
    )

    raise SystemExit(0)


# --------------------------------------------------
# 各記事解析
# --------------------------------------------------

current_items = []


for block in blocks:

    text = block.get_text(
        " ",
        strip=True
    )


    date_match = DATE_RE.search(
        text
    )

    if not date_match:
        continue


    year = int(
        date_match.group(1)
    )

    month = int(
        date_match.group(2)
    )

    day = int(
        date_match.group(3)
    )


    link_element = block.find(
        "a",
        href=True
    )

    if link_element is None:
        continue


    title = link_element.get_text(
        " ",
        strip=True
    )


    title = re.sub(
        r"\s+",
        " ",
        title
    ).strip()


    if not title:
        continue


    link = urljoin(
        SOURCE_URL,
        link_element["href"]
    )


    # --------------------------------------------------
    # カテゴリ
    # --------------------------------------------------

    block_text = block.get_text(
        " ",
        strip=True
    )

    category = ""

    known_categories = [
        "お知らせ",
        "採用情報",
        "医療関係の方",
        "入札情報",
    ]

    for candidate in known_categories:

        if block_text.endswith(
            candidate
        ):

            category = candidate
            break


    # --------------------------------------------------
    # 掲載時刻はサイトにないため正午
    # --------------------------------------------------

    pub_dt = datetime(
        year,
        month,
        day,
        12,
        0,
        0,
        tzinfo=JST
    )


    # --------------------------------------------------
    # GUID
    #
    # 同じURLを複数のお知らせが使うケースがある。
    # 例：センター便りは同じページURL。
    #
    # そのためURLだけをGUIDにしない。
    # 日付＋タイトル＋URLで記事を識別する。
    # --------------------------------------------------

    guid_source = (
        "fukuyama-mc|"
        + f"{year:04d}-{month:02d}-{day:02d}|"
        + title
        + "|"
        + link
    )


    guid = hashlib.sha256(
        guid_source.encode(
            "utf-8"
        )
    ).hexdigest()


    description = ""

    if category:

        description = (
            f"カテゴリ：{category}"
        )


    current_items.append({
        "title": title,
        "link": link,
        "description": description,
        "pubDate": format_datetime(
            pub_dt
        ),
        "guid": guid,
    })


# --------------------------------------------------
# 解析結果チェック
# --------------------------------------------------

if len(current_items) < 10:

    print(
        "有効記事数が異常に少ないため、"
        "既存XMLを維持します。"
    )

    raise SystemExit(0)


print(
    "有効なお知らせ:",
    len(current_items),
    "件"
)


# --------------------------------------------------
# 重複除去
# --------------------------------------------------

unique_items = []
seen = set()


for item in current_items:

    if item["guid"] in seen:
        continue

    seen.add(
        item["guid"]
    )

    unique_items.append(
        item
    )


current_items = unique_items


# --------------------------------------------------
# 既存RSSと統合
#
# 現在ページから消えた古い記事も
# RSS側には残す
# --------------------------------------------------

all_items = []
seen_guids = set()


for item in current_items:

    all_items.append(
        item
    )

    seen_guids.add(
        item["guid"]
    )


for guid, item in old_items.items():

    if guid in seen_guids:
        continue

    all_items.append(
        item
    )

    seen_guids.add(
        guid
    )


# --------------------------------------------------
# 日付順
# --------------------------------------------------

def get_date(item):

    try:

        return parsedate_to_datetime(
            item["pubDate"]
        )

    except Exception:

        return datetime.min.replace(
            tzinfo=timezone.utc
        )


all_items.sort(
    key=get_date,
    reverse=True
)


# --------------------------------------------------
# RSS作成
# --------------------------------------------------

rss = ET.Element(
    "rss",
    version="2.0"
)

channel = ET.SubElement(
    rss,
    "channel"
)


ET.SubElement(
    channel,
    "title"
).text = (
    "福山医療センター お知らせ"
)


ET.SubElement(
    channel,
    "link"
).text = SOURCE_URL


ET.SubElement(
    channel,
    "description"
).text = (
    "福山医療センター「お知らせ」の更新情報"
)


ET.SubElement(
    channel,
    "language"
).text = "ja"


# --------------------------------------------------
# RSS記事
# --------------------------------------------------

for item in all_items:

    element = ET.SubElement(
        channel,
        "item"
    )


    ET.SubElement(
        element,
        "title"
    ).text = item["title"]


    ET.SubElement(
        element,
        "link"
    ).text = item["link"]


    ET.SubElement(
        element,
        "description"
    ).text = item["description"]


    ET.SubElement(
        element,
        "pubDate"
    ).text = item["pubDate"]


    guid_element = ET.SubElement(
        element,
        "guid"
    )

    guid_element.set(
        "isPermaLink",
        "false"
    )

    guid_element.text = item["guid"]


# --------------------------------------------------
# XML保存
# --------------------------------------------------

tree = ET.ElementTree(
    rss
)

ET.indent(
    tree,
    space="  "
)

tree.write(
    OUTPUT,
    encoding="utf-8",
    xml_declaration=True
)


# --------------------------------------------------
# 結果
# --------------------------------------------------

print()
print("=" * 70)
print("RSS作成成功")
print("=" * 70)

print(
    "現在サイトから取得:",
    len(current_items),
    "件"
)

print(
    "RSS保存件数:",
    len(all_items),
    "件"
)

print(
    "保存先:",
    OUTPUT
)


print()
print("最新20件:")


for i, item in enumerate(
    all_items[:20],
    start=1
):

    print()

    print(
        f"[{i}] "
        f"{item['pubDate']} "
        f"{item['title']}"
    )

    if item["description"]:

        print(
            "    ",
            item["description"]
        )

    print(
        "    ",
        item["link"]
    )