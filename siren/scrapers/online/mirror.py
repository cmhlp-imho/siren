from asyncio import Task
import asyncio
from datetime import datetime
import json

from io import StringIO
from pydantic import ValidationError
from siren.core import Model, BaseScraper
from siren.utils import to_thread
from yarl import URL
from bs4 import BeautifulSoup, Tag
from logging import getLogger


logger = getLogger(__name__)

__all__ = (
    "MumbaiMirrorOnlineScraper",
    "BangaloreMirrorOnlineScraper",
    "PuneMirrorOnlineScraper",
)


class MirrorOnlineArticle(Model):
    url: str
    thumbnailUrl: str
    datePublished: datetime
    dateModified: datetime
    headline: str
    description: str
    author: str

    def __eq__(self, other: object):
        if isinstance(other, MirrorOnlineArticle):
            return self.url == other.url
        return False

    def __hash__(self):
        return hash(self.url)


class BaseMirrorOnlineScraper[T: MirrorOnlineArticle](BaseScraper[T]):
    BASE_URL: URL
    model: type[T]

    async def get_search_page(self, query: str, pagenumber: int = 0) -> list[T]:
        url = str(
            self.BASE_URL
            / "getsearchdata.cms"
            % {"query": query, "pagenumber": pagenumber}
        )
        resp = await self.http.get(url)
        if resp.status_code != 200:
            return []
        tasks: list[Task[T | None]] = []
        for url in await self.parse_search_page(resp.text):
            tasks.append(asyncio.create_task(self.get_article(url)))
        return [
            article
            for article in await asyncio.gather(*tasks)
            if article and self.start < article.datePublished < self.end
        ]

    @to_thread
    def parse_search_page(self, html: str) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        div = soup.find("div", class_="searchcontent")
        if clearfix := soup.find("div", class_="Pagination clearfix"):
            clearfix.extract()

        if div:
            urls: list[str] = []
            for tag in div.find_all("a"):  # type: ignore
                assert isinstance(tag, Tag)
                urls.append(str(tag["href"]))
            return urls
        return []

    async def get_article(self, suburl: str) -> T | None:
        url = self.BASE_URL / "news" / suburl.lstrip("/")
        try:
            resp = await self.http.get(str(url))
        except Exception as e:
            logger.error(e)
            return None
        return await self.parse_article(resp.text, str(url))

    @to_thread
    def parse_article(self, html: str, url: str) -> T | None:
        soup = BeautifulSoup(html, "html.parser")
        raw = t.text if (t := soup.find("script", type="application/ld+json")) else "{}"
        data = json.loads(raw, strict=False)
        data["author"] = data.get("author", {}).get("name", "-")
        data.setdefault("thumbnailUrl", "-")
        data.setdefault("headline", "-")
        try:
            return self.model(**data)
        except ValidationError:
            return None

    async def scrape(self) -> list[T]:
        tasks: list[Task[list[T]]] = []
        for keyword in self.keywords:
            for i in range(10, 50):
                tasks.append(asyncio.create_task(self.get_search_page(keyword, i)))
        return list(
            set(article for chunk in await asyncio.gather(*tasks) for article in chunk)
        )


class MumbaiMirrorOnlineScraper(BaseMirrorOnlineScraper[MirrorOnlineArticle]):
    BASE_URL = URL("https://mumbaimirror.indiatimes.com")
    model = MirrorOnlineArticle


class BangaloreMirrorOnlineScraper(BaseMirrorOnlineScraper[MirrorOnlineArticle]):
    BASE_URL = URL("https://bangaloremirror.indiatimes.com")
    model = MirrorOnlineArticle


"""

Pune Mirror Scraper

This Edition of Mirror has a different architecture than the other two, so it has to be scraped seperately.

"""


class Metatags(Model):
    articlePublishedTime: datetime


class Snippet(Model):
    metatags: Metatags


class PuneMirrorArticle(Model):
    cacheUrl: str
    clicktrackUrl: str
    content: str
    contentNoFormatting: str
    title: str
    titleNoFormatting: str
    formattedUrl: str
    unescapedUrl: str
    url: str
    visibleUrl: str
    richSnippet: Snippet

    @property
    def date(self):
        return self.richSnippet.metatags.articlePublishedTime


class PuneMirrorOnlineScraper(BaseScraper[PuneMirrorArticle]):
    model = PuneMirrorArticle

    async def scrape(self) -> list[PuneMirrorArticle]:
        # TODO: Fully Automate this!
        return []
        with open("data.json") as f:
            data = json.load(f)
        result: list[PuneMirrorArticle] = []
        for item in data:
            result.append(PuneMirrorArticle(**item))
        return result

    async def to_csv(
        self,
        *,
        include: set[str] = set(),
        exclude: set[str] = set(),
        aliases: dict[str, str] = {},
    ) -> StringIO:
        include = {"date"}
        exclude = {
            "cacheUrl",
            "clicktrackUrl",
            "content",
            "title",
            "formattedUrl",
            "unescapedUrl",
            "visibleUrl",
            "richSnippet",
        }
        return await super().to_csv(include=include, exclude=exclude, aliases=aliases)
