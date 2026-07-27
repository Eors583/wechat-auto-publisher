# Third-party notices

## wechat-article-exporter

The optional WeChat public-platform backend search provider was implemented
with reference to the request flow documented in
[wechat-article-exporter](https://github.com/wechat-article/wechat-article-exporter),
which is distributed under the MIT License.

Only the management-platform request protocol needed by this project was
reimplemented behind an independent provider boundary; no user credentials,
session data, or bundled application code are included.
