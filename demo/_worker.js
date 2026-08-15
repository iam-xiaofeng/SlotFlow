/**
 * Cloudflare Pages 的路由适配器 —— 和 `demo/serve.py` 做**同一件事**，只是跑在边缘。
 *
 * 静态站点要冒充后端，有两处对不上：
 *
 *   1. `/api/chat/threads` 这种无扩展名路径,既要是端点又要是
 *      `/api/chat/threads/<id>/messages` 的父目录 —— 文件系统里一个名字不能既是文件又是
 *      目录。约定是「每个端点一个目录,内容放 index.json」,这里负责解析。
 *
 *   2. 产物读取接口是 `?path=a/b/c.md` 形式的 query string,静态文件服务器路由不了。
 *      导出时已按 slug 建好目录(`a__b__c.md`),这里把 query 翻成同一个 slug。
 *      —— `_redirects` 表达不了这条规则,这也是必须上 worker 的原因:光靠 `_redirects`,
 *      会话看得到,但产物面板会全 404。
 *
 * 用的是 Pages 的 advanced mode(输出目录根放 `_worker.js`)。
 *
 * **不要再放 `_redirects`**:advanced mode 下它并没有失效,`env.ASSETS.fetch()` 仍然会套用它。
 * 两套规则叠加的后果是双重改写——曾经的 `/api/* → /api/:splat/index.json` 会把这里已经
 * 拼好的 `/api/chat/threads/index.json` 再拼一次,变成 `.../index.json/index.json`,
 * 于是**首页正常但整个 /api 全线 404**。规则只留这一份。
 */

/** 和 `export_demo_api.path_slug` / `serve.py._path_slug` 保持一致。 */
function pathSlug(value) {
  return value.replace(/^\/+|\/+$/g, "").replaceAll("/", "__");
}

/** 端点路径 → 站点里真实存在的文件路径;不是 API 就原样返回。 */
function rewriteApiPath(url) {
  if (!url.pathname.startsWith("/api/")) {
    return null;
  }
  let pathname = url.pathname.replace(/\/+$/, "");

  // 产物读取有两种入口,导出时都落在同一个 slug 目录下:
  //   ?path=a/b/c.md              —— 老形式
  //   /artifacts/raw/a/b/c.md     —— 路径式(带相对引用的 HTML 产物必须用这个,
  //                                  否则 <base href> 做相对解析时会丢掉 query string)
  const prefix = ["/artifacts/read", "/artifacts/raw"].find(
    (item) => pathname.endsWith(item) || pathname.includes(`${item}/`),
  );
  if (prefix) {
    const head = pathname.slice(0, pathname.indexOf(prefix) + prefix.length);
    const rest = pathname.slice(head.length);
    const target = rest ? rest : url.searchParams.get("path");
    if (target) {
      // slug 幂等:已经是 slug 的原样返回,路径式的转成 slug。
      pathname = `${head}/${pathSlug(target)}`;
    }
  }

  return pathname.endsWith(".json") ? pathname : `${pathname}/index.json`;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const apiPath = rewriteApiPath(url);

    let response;
    if (apiPath === null) {
      response = await env.ASSETS.fetch(request);
    } else {
      const rewritten = new URL(url);
      rewritten.pathname = apiPath;
      rewritten.search = "";
      response = await env.ASSETS.fetch(new Request(rewritten, request));
    }

    // 展示站点是只读的,允许被任意页面嵌入/抓取。这条对 `/artifact-assets/` 尤其要紧:
    // 产物面板的 iframe 没有 allow-same-origin(是个不透明源),而 Vite 产出的
    // `<script type="module" crossorigin>` 取资源会强制走 CORS,少了这个头就白屏。
    const headers = new Headers(response.headers);
    headers.set("Access-Control-Allow-Origin", "*");
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers,
    });
  },
};
