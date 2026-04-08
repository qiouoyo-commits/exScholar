#!/usr/bin/env python3
"""
DBLP API 论文获取模块
通过DBLP API获取指定会议或期刊的论文信息(异步版本)

AsyncAbstractFetcher: 异步摘要获取器
- 支持高并发异步请求
- 更高效的I/O处理
- 支持多种方式获取摘要
"""

import json
import logging
import time
import asyncio
import aiohttp
import ssl

from typing import List, Optional, Tuple
from pathlib import Path
from tqdm import tqdm
from fake_useragent import UserAgent
from bs4 import BeautifulSoup
from typing import Literal

from ..driver import PlaywrightDriver, ProxyPool
from .fetch_meta import DBLPMetaFetcher
from ...common.utils import suppress_all_output, info_by_dir
from ..config.venue import get_all_venue_by_rule


class AsyncAbstractFetcher:
    """
    异步论文摘要获取器
    
    使用异步I/O处理，支持高并发请求，显著提高处理效率
    """
    
    def __init__(self, max_concurrent: int = 10, proxy_pool_size: int = 10):
        """ 
        初始化异步摘要获取器
        
        Args:
            max_concurrent: 最大并发请求数
        """
        self.crossref_base_url = "https://api.crossref.org/works/"
        self.openalex_base_url = "https://api.openalex.org/works/"
        self.semantic_scholar_base_url = "https://api.semanticscholar.org/v1/paper/"
        self.ua = UserAgent()
        self.driver = PlaywrightDriver(max_concurrent=10, proxy_pool_size=10, headless=True, timeout=120000)

        # 线程池
        self.proxy_pool = ProxyPool(pool_size=proxy_pool_size)

        # 异步配置
        self.max_concurrent = max_concurrent
        self.semaphore = None
        self.session = None
        
        # 统计信息
        self.stats_map = {}
        self.stats_keys = [
            'total_papers',
            'papers_with_abstract',
            'papers_without_doi',
            'papers_without_doi_and_url',
            'papers_abstract_fetched',
            'papers_abstract_failed'
        ]

    async def __aenter__(self):
        """异步上下文管理器入口"""
        # 创建SSL上下文，禁用证书验证以解决arxiv.org等网站的SSL问题
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        
        # 创建TCP连接器，使用自定义SSL上下文
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        
        self.session = aiohttp.ClientSession(
            connector=connector,
            headers={
                'User-Agent': self.ua.random,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
            },
            timeout=aiohttp.ClientTimeout(total=12)
        )
        self.semaphore = asyncio.Semaphore(self.max_concurrent)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        if self.session:
            await self.session.close()
    
    def _clean_abstract(self, abstract: str) -> str:
        """
        清理摘要文本，移除HTML标签和多余空白字符
        
        Args:
            abstract: 原始摘要文本
            
        Returns:
            清理后的摘要文本
        """
        import re
        # 移除HTML标签
        abstract = re.sub(r'<[^>]+>', '', abstract)
        # 移除多余的空白字符
        abstract = re.sub(r'\s+', ' ', abstract).strip()
        return abstract
    
    async def _request_with_retry_async(self, url: str, doi: str, api_name: str = "API", retry_delays: List[float] = [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 3, 10]) -> Optional[dict]:
        """
        异步带重试机制的API请求
        
        Args:
            url: API请求URL
            doi: 论文DOI（用于日志）
            api_name: API名称（用于日志）
            retry_delays: 重试延迟时间列表
            
        Returns:
            API响应的JSON数据，如果失败返回None
        """
        # 重试配置：延迟时间（秒）
        max_retries = len(retry_delays)
        
        async with self.semaphore:  # 限制并发数
            for attempt in range(max_retries + 1):
                try:
                    async with self.session.get(
                        url, 
                        proxy=self.proxy_pool.get_proxy_url(),
                        timeout=aiohttp.ClientTimeout(total=3.6)
                    ) as response:
                        if response.status == 404:
                            return None
                        response.raise_for_status()
                        return await response.json()
                        
                except Exception as e:
                    if attempt < max_retries:
                        delay = retry_delays[attempt]
                        # logging.warning(f"{api_name}获取摘要时发生错误 {doi} (尝试 {attempt + 1}/{max_retries + 1}): {e}")
                        await asyncio.sleep(delay)
                    else:
                        logging.error(f"{api_name}获取摘要最终失败，已重试 {max_retries} 次: {doi} - {e}")
                        return None
        
        return None
    
    async def _request_html_with_retry_async(self, url: str, source_name: str = "网页") -> Optional[str]:
        """
        异步带重试机制的HTML页面请求
        
        Args:
            url: 网页URL
            source_name: 来源名称（用于日志）
            
        Returns:
            网页的HTML内容，如果失败返回None
        """
        # 重试配置：延迟时间（秒）
        retry_delays = [0.1, 0.1, 0.1, 0.1, 0.1, 0.1]
        max_retries = len(retry_delays)
        
        async with self.semaphore:  # 限制并发数
            for attempt in range(max_retries + 1):
                try:
                    async with self.session.get(url, proxy=self.proxy_pool.get_proxy_url(with_auth=True)) as response:
                        if response.status == 404:
                            return None
                        response.raise_for_status()
                        return await response.text()
                        
                except Exception as e:
                    if attempt < max_retries:
                        delay = retry_delays[attempt]
                        if delay > 1:
                            print(f"⚠️ {source_name} 获取页面失败，等待 {delay} 秒后重试: {url}, 错误信息: {e}")
                        await asyncio.sleep(delay)
                    else:
                        logging.error(f"{source_name}获取页面最终失败，已重试 {max_retries} 次: {url} - {e}")
                        return None
        
        return None

    # ========================== 多方式通过 url 获取摘要 ==========================

    async def fetch_abstract_from_acm(self, url: str) -> Optional[str]:
        """
        异步从ACM 页面获取论文摘要
        """
        with suppress_all_output():
            html = await self.driver.safe_get(url, ['#abstract', '.abstractSection'], max_retries=5)
        if html is None:
            return None
        
        # 使用BeautifulSoup解析HTML
        soup = BeautifulSoup(html, 'html.parser')
        
        # 1. 查找摘要section - ACM页面通常有id为"abstract"的section
        abstract_section = soup.find('section', id='abstract')
        if abstract_section:
            # 查找所有段落元素
            paragraphs = abstract_section.find_all('div', role='paragraph')
            if paragraphs:
                # 将所有段落文本合并
                abstract_text = ' '.join([p.get_text(strip=True) for p in paragraphs])
                return abstract_text

        # 2. 查找摘要 div - class 为 abstractSection
        abstract_div = soup.find('div', class_='abstractSection')
        if abstract_div:
            # 查找所有段落元素
            paragraphs = abstract_div.find_all('p')
            if paragraphs:
                # 将所有段落文本合并
                abstract_text = ' '.join([p.get_text(strip=True) for p in paragraphs])
                return abstract_text

        return None

    async def fetch_abstract_from_acl(self, url: str) -> Optional[str]:
        """
        异步从 ACL Anthology 页面获取论文摘要

        部分文章不包含摘要
        """

        def fetch_abstract_from_acl_html(html: str) -> str:
            if html is None: return None

            # 使用BeautifulSoup解析HTML
            soup = BeautifulSoup(html, 'html.parser')
            
            # 查找摘要元素 - 通常包含在class为"acl-abstract"的div中
            abstract_div = soup.find('div', class_='acl-abstract')
            if abstract_div:
                # 查找摘要内容 - 通常在span标签中
                abstract_span = abstract_div.find('span')
                if abstract_span:
                    # 获取摘要文本并清理
                    abstract_text = abstract_span.get_text(strip=True)
                    return abstract_text
            return None

        # ================ 使用 request 获取 ================
        html = await self._request_html_with_retry_async(url, "ACL Anthology")
        abstract_text = fetch_abstract_from_acl_html(html)
        if abstract_text:
            return abstract_text

        # ================ 使用 playwright 获取 ================
        with suppress_all_output():
            html = await self.driver.safe_get(url, [], max_retries=5)
        abstract_text = fetch_abstract_from_acl_html(html)
        if abstract_text:
            return abstract_text

        return None

    async def fetch_abstract_from_openaccess(self, url: str) -> Optional[str]:
        """
        异步从 openaccess 页面获取论文摘要 (cvpr)
        """
        html = await self._request_html_with_retry_async(url, "OpenAccess")
        if html is None:
            return None
        
        # 使用BeautifulSoup解析HTML
        soup = BeautifulSoup(html, 'html.parser')
        
        # 查找摘要元素 - 通常包含 id="abstract" 的 div 内的 p 标签
        abstract_div = soup.find('div', id='abstract')
        if abstract_div:
            abstract_text = abstract_div.get_text(strip=True)
            return abstract_text
        return None

    async def fetch_abstract_from_usenix(self, url: str) -> Optional[str]:
        """
        异步从 usenix 页面获取论文摘要 (FAST, NSDI, OSDI)
        """
        html = await self._request_html_with_retry_async(url, "OpenAccess")
        if html is None:
            return None
        
        # 使用BeautifulSoup解析HTML
        soup = BeautifulSoup(html, 'html.parser')
        
        # 查找摘要元素 - 完整的class匹配
        abstract_div = soup.find('div', class_='field field-name-field-paper-description field-type-text-long field-label-above')
        if abstract_div:
            # 查找 field-items 容器
            field_items = abstract_div.find('div', class_='field-items')
            if field_items:
                # 查找 field-item 内容
                field_item = field_items.find('div', class_='field-item')
                if field_item:
                    # 提取所有p标签的文本内容
                    paragraphs = field_item.find_all('p')
                    if paragraphs:
                        # 合并所有段落，用换行符分隔
                        abstract_parts = []
                        for p in paragraphs:
                            text = p.get_text(strip=True)
                            if text:  # 过滤空段落
                                abstract_parts.append(text)
                        if abstract_parts:
                            return ' '.join(abstract_parts)
                    
                    # 如果没有p标签，直接获取文本
                    abstract_text = field_item.get_text(strip=True)
                    # 清理掉可能的标签残留
                    if abstract_text and not abstract_text.startswith('Abstract:'):
                        return abstract_text
        
        return None

    async def fetch_abstract_from_openreview(self, url: str) -> Optional[str]:
        """
        异步从 OpenReview 页面获取论文摘要
        """
        html = await self._request_html_with_retry_async(url, "OpenReview")
        if html is None:
            return None
        
        soup = BeautifulSoup(html, 'html.parser')
        
        # 查找摘要元素 - OpenReview的摘要在note-content中
        # 寻找包含"Abstract"的strong标签，文本可能被分割成多个节点
        abstract_strongs = soup.find_all('strong', class_='note-content-field')
        for strong in abstract_strongs:
            # 获取完整的文本内容，包括分段的文本节点
            full_text = strong.get_text(strip=True)
            if 'Abstract' in full_text and ':' in full_text:
                # 在同一个父容器中查找note-content-value
                parent = strong.parent
                if parent:
                    # 查找markdown渲染的内容
                    markdown_content = parent.find(class_='note-content-value')
                    if markdown_content:
                        # 提取所有p标签的文本内容
                        paragraphs = markdown_content.find_all('p')
                        if paragraphs:
                            abstract_parts = []
                            for p in paragraphs:
                                text = p.get_text(strip=True)
                                if text:
                                    abstract_parts.append(text)
                            if abstract_parts:
                                return ' '.join(abstract_parts)
                        
                        # 如果没有p标签，直接获取文本
                        abstract_text = markdown_content.get_text(strip=True)
                        if abstract_text:
                            return abstract_text

        return None

    async def fetch_abstract_from_mlr(self, url: str) -> Optional[str]:
        """
        异步从 Proceedings of Machine Learning Research 页面获取论文摘要
        """
        html = await self._request_html_with_retry_async(url, "MLR")
        if html is None:
            return None
        
        soup = BeautifulSoup(html, 'html.parser')
        
        # 查找摘要元素 - MLR的摘要在 id=abstract 的 div 中
        abstract_div = soup.find('div', id='abstract')
        if abstract_div:
            abstract_text = abstract_div.get_text(strip=True)
            if abstract_text:
                return abstract_text

        return None

    async def fetch_abstract_from_ijcai(self, url: str) -> Optional[str]:
        """
        异步从 ijcai 页面获取论文摘要 (ijcai)

        有些文章的页面结构不太一样
        """
        html = await self._request_html_with_retry_async(url, "IJCAI")
        if html is None:
            return None
        
        # 使用BeautifulSoup解析HTML
        soup = BeautifulSoup(html, 'html.parser')
        
        # 1. 包含 ijcai.org/proceedings/xxx
        proceedings_detail = soup.find('div', class_='container-fluid proceedings-detail')
        if proceedings_detail:
            # 查找所有的row
            rows = proceedings_detail.find_all('div', class_='row')
            if len(rows) >= 3:  # 第三个row包含摘要内容
                abstract_row = rows[2]  # 索引从0开始，第三个row
                # 查找col-md-12容器
                col_divs = abstract_row.find_all('div', class_='col-md-12')
                if col_divs:
                    # 第一个col-md-12通常包含摘要文本
                    abstract_div = col_divs[0]
                    abstract_text = abstract_div.get_text(strip=True)
                    
                    # 过滤掉Keywords部分
                    if 'Keywords:' in abstract_text:
                        abstract_text = abstract_text.split('Keywords:')[0].strip()
                    
                    if abstract_text:
                        return abstract_text
        
        # 2. 包含 ijcai.org/Abstract/xxx
        content_detail = soup.find('div', class_='region region-content')
        if content_detail:
            # 查找 class=content 的 div 容器
            content_div = content_detail.find('div', class_='content')
            if content_div:
                # 查找第二个 p 标签
                p_tags = content_div.find_all('p')
                if len(p_tags) >= 2:
                    abstract_text = p_tags[1].get_text(strip=True)
                    if abstract_text:
                        return abstract_text
        
        return None

    async def fetch_abstract_from_ndss(self, url: str) -> Optional[str]:
        """
        异步从 ndss 页面获取论文摘要 (ndss)
        """
        html = await self._request_html_with_retry_async(url, "NDSS")
        if html is None:
            return None
        
        # 使用BeautifulSoup解析HTML
        soup = BeautifulSoup(html, 'html.parser')
        
        # 1. 包含 ndss-paper/xxx
        entry_content = soup.find('div', class_='entry-content')
        if entry_content:
            # 查找paper-data容器
            paper_data = entry_content.find('div', class_='paper-data')
            if paper_data:
                # 只找到直接子级的p标签，避免嵌套p标签导致的重复
                p_tags = paper_data.find_all('p', recursive=False)
                
                abstract_parts = []
                found_author_section = False
                
                for p in p_tags:
                    # 检查是否包含strong标签（作者信息）
                    if p.find('strong'):
                        found_author_section = True
                        continue
                    
                    # 如果已经找到作者部分，开始收集摘要文本
                    if found_author_section:
                        # 对于嵌套的p标签，获取内部所有p标签的文本
                        inner_p_tags = p.find_all('p')
                        if inner_p_tags:
                            # 如果有内部p标签，提取它们的文本
                            for inner_p in inner_p_tags:
                                text = inner_p.get_text(strip=True)
                                if text:
                                    abstract_parts.append(text)
                        else:
                            # 如果没有内部p标签，直接获取文本
                            text = p.get_text(strip=True)
                            if text:
                                abstract_parts.append(text)
                
                if abstract_parts:
                    # 合并所有摘要段落，并去重
                    unique_parts = []
                    seen = set()
                    for part in abstract_parts:
                        if part not in seen:
                            seen.add(part)
                            unique_parts.append(part)

                    if unique_parts:
                        abstract_text = ' '.join(unique_parts)
                        return abstract_text
        
        # 2. 其他 - 处理包含 "Abstract:" 的新格式页面
        section_content = soup.find('section', class_='new-wrapper')
        if section_content:
            # 查找包含 "Abstract:" 的 h2 标签
            abstract_h2 = section_content.find('h2', string=lambda text: text and 'Abstract:' in text)
            if abstract_h2:
                # 收集 h2 标签后面的所有 p 标签中的文本
                abstract_parts = []
                next_element = abstract_h2.find_next_sibling() # 不会越界，只查找与当前元素同级的下一个兄弟元素
                
                while next_element:
                    if next_element.name == 'p':
                        # 获取 p 标签的文本内容
                        text = next_element.get_text(strip=True)
                        if text:
                            abstract_parts.append(text)
                    next_element = next_element.find_next_sibling()
                
                if abstract_parts:
                    # 使用空格连接所有摘要段落
                    abstract_text = ' '.join(abstract_parts)
                    return abstract_text

        return None
    
    async def fetch_abstract_from_nips(self, url: str) -> Optional[str]:
        """
        异步从 NIPS proceedings 页面获取论文摘要
        """
        html = await self._request_html_with_retry_async(url, "NIPS")
        if html is None:
            return None
        
        # 使用BeautifulSoup解析HTML
        soup = BeautifulSoup(html, 'html.parser')
        
        # 查找摘要元素 - 查找h4标题为"Abstract"的元素
        abstract_h4 = soup.find('h4', string='Abstract')
        if abstract_h4:
            # 查找紧跟在Abstract标题后的p标签
            next_element = abstract_h4.find_next_sibling()
            while next_element:
                if next_element.name == 'p':
                    # 提取p标签内的所有文本内容
                    abstract_text = next_element.get_text(strip=True)
                    if abstract_text:
                        return abstract_text
                    break
                next_element = next_element.find_next_sibling()
        
        return None

    async def fetch_abstract_from_arxiv(self, url: str) -> Optional[str]:
        """
        异步从 arxiv 页面获取论文摘要
        """
        html = await self._request_html_with_retry_async(url, "arXiv")
        if html is None:
            return None
        
        # 使用BeautifulSoup解析HTML
        soup = BeautifulSoup(html, 'html.parser')
        
        # 查找摘要元素 - 查找class为"abstract mathjax"的blockquote元素
        abstract_blockquote = soup.find('blockquote', class_='abstract mathjax')
        if abstract_blockquote:
            # 查找Abstract:标签
            descriptor_span = abstract_blockquote.find('span', class_='descriptor')
            if descriptor_span and 'Abstract:' in descriptor_span.get_text():
                # 获取blockquote内的所有文本，但排除descriptor span的文本
                abstract_text = abstract_blockquote.get_text(strip=True)
                # 移除"Abstract:"前缀
                if abstract_text.startswith('Abstract:'):
                    abstract_text = abstract_text[9:].strip()
                return abstract_text
        
        return None

    async def fetch_abstract_from_springer(self, url: str) -> Optional[str]:
        """
        异步从 springer 页面获取论文摘要
        """
        html = await self._request_html_with_retry_async(url, "Springer")
        if html is None:
            return None
        
        # 使用BeautifulSoup解析HTML
        soup = BeautifulSoup(html, 'html.parser')
        
        # 查找摘要元素 - 查找 id 为 Abs1-content 的 div 元素，其内所有 p 的 text 即为摘要
        abstract_div = soup.find('div', id='Abs1-content')
        if abstract_div:
            # 获取所有 p 标签的文本
            abstract_text = ' '.join([p.get_text(strip=True) for p in abstract_div.find_all('p')])
            return abstract_text
        
        return None

    async def fetch_abstract_from_ieee(self, url: str) -> Optional[str]:
        """
        异步从 ieee 页面获取论文摘要
        """
        with suppress_all_output():
            # html = await self.driver.safe_get(url, ['.u-mb-1'], max_retries=5)
            html = await self.driver.safe_get(url, [], max_retries=5)
        # html = await self._request_html_with_retry_async(url, "IEEE")
        if html is None:
            return None
        
        # 使用BeautifulSoup解析HTML
        soup = BeautifulSoup(html, 'html.parser')
        
        # 方法1: 先查找具有u-mb-1 class的父容器 (只包含 u-mb-1)，确保找到正确的摘要区域
        abstract_containers = soup.find_all('div', class_='u-mb-1')

        # 找到 abstract_container 中包含 abstract 的容器
        abstract_container = None
        for abstract_container in abstract_containers:
            if 'abstract' in abstract_container.get_text(strip=True).lower():
                break # 找到第一个包含 abstract 的容器
        
        if abstract_container:
            # 在该容器内查找包含 "Abstract" 的 h2 标签
            abstract_h2 = abstract_container.find('h2', string=lambda text: text and 'Abstract' in text.strip())
            if abstract_h2:
                # 在同一容器内查找带有xplmathjax属性的div
                abstract_div = abstract_container.find('div', attrs={'xplmathjax': True})
                if abstract_div:
                    abstract_text = abstract_div.get_text(strip=True)
                    if abstract_text:
                        return abstract_text
                
                # 如果没找到xplmathjax属性的div，查找h2后面紧跟的div
                next_element = abstract_h2.find_next_sibling()
                while next_element:
                    if next_element.name == 'div':
                        abstract_text = next_element.get_text(strip=True)
                        if abstract_text:
                            return abstract_text
                        break
                    next_element = next_element.find_next_sibling()
        
        return None

    async def fetch_abstract_from_aaai(self, url: str) -> Optional[str]:
        """
        异步从 aaai 页面获取论文摘要
        """
        with suppress_all_output():
            html = await self.driver.safe_get(url, [], max_retries=5)
        # html = await self._request_html_with_retry_async(url, "AAAI")
        if html is None:
            return None
        
        # 使用BeautifulSoup解析HTML
        soup = BeautifulSoup(html, 'html.parser')
        
        # 1. 查找摘要元素 - 查找 class = 'item abstract' 的 section 元素
        abstract_section = soup.find('section', class_='item abstract')
        if abstract_section:
            # 先移除 section 内的 h2 标签
            h2_tag = abstract_section.find('h2')
            if h2_tag: h2_tag.decompose()

            # 获取剩余内容作为摘要
            abstract_text = abstract_section.get_text(strip=True)
            if abstract_text:
                return abstract_text
        
        # 2. 第二种网页形式 - 查找 paper-section-wrap 结构
        # 查找包含 "Abstract:" 的 h4 标签的父容器
        abstract_containers = soup.find_all('div', class_='paper-section-wrap')
        for container in abstract_containers:
            h4_tag = container.find('h4')
            if h4_tag and 'Abstract:' in h4_tag.get_text(strip=True):
                # 在该容器内查找 attribute-output 的 div
                attribute_output = container.find('div', class_='attribute-output')
                if attribute_output:
                    # 获取 p 标签中的文本内容
                    p_tag = attribute_output.find('p')
                    if p_tag:
                        abstract_text = p_tag.get_text(strip=True)
                        if abstract_text:
                            return abstract_text

        return None

    async def fetch_abstract_by_url_async(self, url: str, venue_name: str = None) -> Optional[str]:
        """
        异步使用多个API源获取摘要

        Args:
            url: 论文URL
            
        Returns:
            论文摘要文本，如果所有API都获取失败返回None
        """

        if 'pdf' in url:
            return None

        if 'aclanthology' in url or 'findings-acl' in url or 'acl' in url:
            return await self.fetch_abstract_from_acl(url)
        elif 'dl.acm.org' in url:
            return await self.fetch_abstract_from_acm(url)
        elif 'openaccess' in url:
            return await self.fetch_abstract_from_openaccess(url)
        elif 'ijcai' in url:
            return await self.fetch_abstract_from_ijcai(url)
        elif 'usenix' in url:
            return await self.fetch_abstract_from_usenix(url)
        elif 'ndss' in url:
            return await self.fetch_abstract_from_ndss(url)
        elif 'nips' in url or 'neurips' in url:
            return await self.fetch_abstract_from_nips(url)
        elif 'arxiv' in url:
            return await self.fetch_abstract_from_arxiv(url)
        elif 'openreview' in url:
            return await self.fetch_abstract_from_openreview(url)
        elif 'proceedings.mlr' in url:
            return await self.fetch_abstract_from_mlr(url)
        elif 'springer' in url:
            return await self.fetch_abstract_from_springer(url)
        elif 'ieee' in url:
            return await self.fetch_abstract_from_ieee(url)
        elif 'aaai' in url:
            return await self.fetch_abstract_from_aaai(url)
        
        # 特殊情况
        elif 'doi.org' in url and venue_name and venue_name in ['crypto', 'eurocrypt', 'fm', 'cav', 'wine', 'eccv']:
            return await self.fetch_abstract_from_springer(url)
        elif 'doi.org' in url and venue_name and venue_name in ['mm', 'icmr']:
            return await self.fetch_abstract_from_acm(url)
        elif 'doi.org' in url and venue_name and venue_name in ['emnlp', 'naacl', 'acl']:
            return await self.fetch_abstract_from_acl(url)
        elif 'doi.org' in url and venue_name and venue_name in ['icaps']:
            return await self.fetch_abstract_from_aaai(url)
        elif 'doi.org' in url and venue_name and venue_name in ['icassp', 'icme']:
            return await self.fetch_abstract_from_ieee(url)

        # 其他情况
        else:
            return None

    # ========================== 多方式通过 doi 获取摘要 ==========================

    async def fetch_abstract_from_crossref(self, doi: str) -> Optional[str]:
        """
        异步从CrossRef API获取论文摘要
        
        Args:
            doi: 论文DOI
            
        Returns:
            论文摘要文本，如果获取失败返回None
        """
        url = f"{self.crossref_base_url}{doi}"
        data = await self._request_with_retry_async(url, doi, "CrossRef", retry_delays=[0.1, 0.1])
        
        if data is None:
            return None
        
        message = data.get('message', {})
        abstract = message.get('abstract', '')
        
        if abstract:
            return self._clean_abstract(abstract)
        
        return None
    
    async def fetch_abstract_from_openalex(self, doi: str) -> Optional[str]:
        """
        异步从OpenAlex API获取论文摘要
        
        Args:
            doi: 论文DOI
            
        Returns:
            论文摘要文本，如果获取失败返回None
        """
        url = f"{self.openalex_base_url}doi:{doi}"
        data = await self._request_with_retry_async(url, doi, "OpenAlex", retry_delays=[0.1, 0.1])
        
        if data is None:
            return None
        
        # OpenAlex返回的摘要可能在abstract_inverted_index字段中
        abstract_inverted_index = data.get('abstract_inverted_index', {})
        
        if abstract_inverted_index:
            # 将倒排索引转换为完整文本
            word_positions = []
            for word, positions in abstract_inverted_index.items():
                for pos in positions:
                    word_positions.append((pos, word))
            
            # 按位置排序
            word_positions.sort(key=lambda x: x[0])
            
            # 重建摘要文本
            abstract = ' '.join([word for _, word in word_positions])
            
            if abstract:
                return self._clean_abstract(abstract)
        
        return None
    
    async def fetch_abstract_from_semantic_scholar(self, doi: str) -> Optional[str]:
        """
        异步从Semantic Scholar API获取论文摘要
        
        Args:
            doi: 论文DOI
            
        Returns:
            论文摘要文本，如果获取失败返回None
        """
        url = f"{self.semantic_scholar_base_url}{doi}"
        data = await self._request_with_retry_async(url, doi, "Semantic Scholar", retry_delays=[0.1, 0.1])
        
        if data is None:
            return None
        
        # Semantic Scholar返回的摘要在abstract字段中
        abstract = data.get('abstract', '')
        
        if abstract:
            return self._clean_abstract(abstract)
        
        return None
    
    async def fetch_abstract_by_doi_async(self, doi: str) -> Optional[str]:
        """
        异步使用多个API源获取摘要，具有回退机制
        
        Args:
            doi: 论文DOI
            
        Returns:
            论文摘要文本，如果所有API都获取失败返回None
        """
        # 1. 尝试OpenAlex API
        abstract = await self.fetch_abstract_from_openalex(doi)
        if abstract:
            return abstract

        # 2. 尝试Semantic Scholar API
        # abstract = await self.fetch_abstract_from_semantic_scholar(doi)
        # if abstract:
        #     return abstract

        # 3. 尝试CrossRef API
        abstract = await self.fetch_abstract_from_crossref(doi)
        if abstract:
            return abstract
        
        return None
    
    # ========================== 多级别处理论文 ==========================

    async def process_paper_async(self, paper: dict, json_file: Path) -> bool:
        """
        异步处理单篇论文的摘要获取
        
        Args:
            paper: 论文数据字典
            json_file: JSON文件路径
            
        Returns:
            是否成功获取到摘要
        """
        # 1. 处理文件信息
        if json_file not in self.stats_map:
            self.stats_map[json_file] = {key: 0 for key in self.stats_keys}

        self.stats_map[json_file]['total_papers'] += 1
        venue_name = json_file.name.split('_')[0]
        
        # 2. 信息检查
        # 检查是否有DOI
        doi = paper.get('doi', '')
        url = paper.get('ee', [''])[0]  # 假设ee是一个列表，取第一个元素

        if not doi and not url:
            self.stats_map[json_file]['papers_without_doi_and_url'] += 1
            self.stats_map[json_file]['papers_without_doi'] += 1
            return False
        
        if not doi:
            self.stats_map[json_file]['papers_without_doi'] += 1

        # 检查是否已有摘要
        if 'abstract' in paper and paper['abstract']:
            self.stats_map[json_file]['papers_with_abstract'] += 1
            return False
        
        # 3. 通过多个API源获取摘要（具有回退机制）
        abstract = None
        # must_url_venues = ['eccv', 'emnlp'] # eccv 的 doi 检索不到
        must_url_venues = ['icme', 'icassp'] # 临时用，有些会议在最新的年份中 doi 没办法拿到数据
        if doi and venue_name not in must_url_venues:
            abstract = await self.fetch_abstract_by_doi_async(doi)
        if url and not abstract:
            abstract = await self.fetch_abstract_by_url_async(url, venue_name)
        if not abstract:
            self.stats_map[json_file]['papers_abstract_failed'] += 1
            return False

        paper['abstract'] = abstract
        self.stats_map[json_file]['papers_abstract_fetched'] += 1
        return True
    
    async def process_papers_async(self, papers: List[dict], json_file: Path) -> Tuple[int, int]:
        """
        异步并发处理一批论文的摘要获取
        
        Args:
            papers: 论文数据列表
            json_file: JSON文件路径
            
        Returns:
            (成功处理的论文数, 总论文数)
        """
        # 初始化统计信息
        if json_file not in self.stats_map:
            self.stats_map[json_file] = {key: 0 for key in self.stats_keys}
        self.stats_map[json_file]['total_papers'] += len(papers)

        # 过滤得到没有摘要的论文
        filted_papers = [
            paper for paper in papers
            if (not paper.get('abstract') and paper.get('type', '') != 'Editorship')
        ]
        if len(filted_papers) == 0:
            return 0, len(filted_papers)
        
        # 并发处理所有论文，这样用的前提是底层一定共用 semaphore 来限制并发数量
        tasks = [asyncio.create_task(self.process_paper_async(paper, json_file)) for paper in filted_papers]
        pbar = tqdm(total=len(filted_papers), desc=f"Processing {json_file.name}", unit=" paper", leave=False)

        successful = 0
        for coro in asyncio.as_completed(tasks):
            res = await coro
            if res:
                successful += 1
            pbar.update(1)
        pbar.close()
        return successful, len(filted_papers)
        
    async def process_file_async(self, json_file: Path) -> bool:
        """
        异步处理单个JSON文件
        
        Args:
            json_file: JSON文件路径
            
        Returns:
            文件是否被更新
        """
        try:
            # 读取JSON文件
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            papers = data.get('papers', [])
            
            # 使用异步处理
            successful_count, total_count = await self.process_papers_async(papers, json_file)
            file_updated = successful_count > 0
            
            logging.info(f"📊 文件 {json_file.name}: 成功处理 {successful_count}/{total_count} 篇论文")

            # 如果文件有更新，保存回去
            if file_updated:
                with open(json_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            
            return file_updated
            
        except Exception as e:
            logging.error(f"❌ 处理文件失败: {json_file.name} - {e}")
            return False
    
    async def process_dir_async(self, data_dir: str):
        """
        异步执行摘要获取流程
        """
        # 获取所有JSON文件
        json_files = list(Path(data_dir).glob("*.json"))
        
        if not json_files:
            logging.error(f"❌ 目录中没有找到JSON文件: {data_dir}")
            return
        
        logging.info(f"📁 处理目录: {data_dir}: {len(json_files)} 个文件")
        
        # 逐个处理文件
        # tmp_confs = ['icassp', 'naacl', 'icaps']
        # tmp_confs = ['icassp', 'icaps']
        # tmp_confs = ['emnlp', 'naacl']
        for json_file in sorted(json_files):
            # if any(tmp_conf in str(json_file.name) for tmp_conf in tmp_confs):
            #     await self.process_file_async(json_file)
            await self.process_file_async(json_file)
        
        # 统计处理结果
        logging.info(f"✅ 成功处理 {len(json_files)} 个文件")
        
        # 输出统计信息
        self.print_stats()

    def print_stats(self):
        """
        打印统计信息
        """
        logging.info("=" * 80)
        logging.info("📊 异步摘要获取统计:")
        
        total_stats = {key: 0 for key in self.stats_keys}
        for file_stats in self.stats_map.values():
            for key in self.stats_keys:
                total_stats[key] += file_stats.get(key, 0)
        
        logging.info(f"📄 总论文数: {total_stats['total_papers']:,}")
        logging.info(f"✅ 已有摘要: {total_stats['papers_with_abstract']:,}")
        logging.info(f"❌ 无DOI信息: {total_stats['papers_without_doi']:,}")
        logging.info(f"🆕 新获取摘要: {total_stats['papers_abstract_fetched']:,}")
        logging.info(f"⚠️ 获取失败: {total_stats['papers_abstract_failed']:,}")
        logging.info("=" * 80)

        # 保存统计信息到文件 - 修复：将 Path 对象转换为字符串
        # stats_for_json = {}
        # for path_obj, stats in self.stats_map.items():
        #     # 将 Path 对象转换为字符串作为键
        #     stats_for_json[str(path_obj)] = stats

        # with open('stats.json', 'w', encoding='utf-8') as f:
        #     json.dump(stats_for_json, f, ensure_ascii=False, indent=2)

async def main_papers_abstract(data_dir: str, max_concurrent: int = 100, proxy_pool_size: int = 10):
    """
    使用异步处理获取论文摘要的主函数
    
    Args:
        data_dir: 数据目录路径
        max_concurrent: 最大并发请求数
    """
    async with AsyncAbstractFetcher(max_concurrent, proxy_pool_size) as fetcher:
        # 启动浏览器
        await fetcher.driver.start()
        # 1. 获取所有需要处理的会议
        await fetcher.process_dir_async(data_dir)
        # 关闭浏览器
        await fetcher.driver.close()
