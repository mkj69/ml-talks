# ML Talks

一个像 YouTube 一样浏览 AI 顶会论文与报告视频的开源网站，手机上也能看。

- **覆盖范围**
  - 机器学习：NeurIPS 2013–、ICML 2017–、ICLR 2018–，内容包括 oral、spotlight、poster、tutorial、invited talk、workshop 和 competition。
  - 计算机视觉：CVPR 2023–、ICCV 2025、ECCV 2024–。
  - NLP：ACL 2017–、EMNLP 2016–、NAACL 2018–、EACL 2021–，以及 Findings 2020–。
- **数据来源**
  - ML 和 CV 会议：来自会议官方 virtual 站点的公开元数据 JSON，以及每个活动页面里嵌入或链接的视频，来源有 SlidesLive、YouTube、Vimeo 和 Facebook。
  - NLP 会议：来自 [ACL Anthology](https://github.com/acl-org/acl-anthology) 的 XML，视频是 Anthology 官方托管的 mp4 或 SlidesLive 链接。
- **不存储视频**：页面里嵌入的是官方播放器，版权和访问规则仍归会议所有。有些视频（尤其是当年刚开完的会议）需要在官网登录或注册后才能看，这时网站会给出“会议官网页面”的链接。
- **手机端**：单列信息流，播放器吸顶。可以“添加到主屏幕”当 App 用（PWA），收藏和观看历史保存在本机。

## 自动更新

`.github/workflows/update.yml` 每周一自动运行，流程如下：

1. 运行 `scraper/build.py`。它会重新下载官方元数据，只抓新出现的页面，以及之前还没有视频、需要复查的页面。
2. 把爬取缓存（`data/pages/`、`data/listings/`）提交回仓库。
3. 生成索引文件并部署到 GitHub Pages。

仓库里已经带有完整的爬取缓存，所以第一次部署只需几分钟，不用从头抓取。

## 部署到你自己的 GitHub

1. 新建仓库，推送本目录。
2. 在仓库的 Settings → Pages 里，把 Source 设为 **GitHub Actions**。
3. 在 Actions 里手动运行一次 “Update index and deploy”。

## 本地运行

```bash
python3 scraper/build.py --conf iclr --year 2024   # 只抓一个会议年份
python3 scraper/build.py                           # 全部（有缓存时几分钟；可中断，下次接着抓）
./serve.sh                                         # 打开 http://localhost:8000
```

爬虫只用到 Python 标准库。默认 4 个并发、每个请求后等待 0.5 秒，对官方站点比较友好。

## 数据格式

提交到仓库的是爬取缓存：

- `data/pages/<conf>-<year>.json`：每个活动页面的抓取结果（视频 id、缩略图等）
- `data/listings/<conf>-<year>.json`：活动列表页的抓取结果

以下文件由 `build.py` 生成，不提交到仓库：

- `data/manifest.json`：每个会议年份的条目数和有视频的条目数
- `data/papers/<conf>-<year>.json`：列表数据。`vs` 是视频列表，格式为 `[来源, id]`，来源取值 `sl` / `yt` / `vm` / `fb` / `mp4`
- `data/abstracts/<conf>-<year>.json`：摘要，打开视频页时才加载

欢迎 PR：补充更早年份的来源（比如 videolectures.net 上 2013 年之前的 NIPS、2023 年之前的 CVPR），或者增加其他会议（CoRL、RSS、COLT、KDD 等）。
