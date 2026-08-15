/**
 * 展示页专用补丁 —— 只作用于 `demo/build.sh` 产出的静态站点，**不进产品源码**。
 *
 * 静态实录页和真实产品的取舍不一样：真实产品里这两个默认值是对的（收起工具时间线免得刷屏、
 * 不自动弹面板免得挡住对话），但展示页的目的就是**让人一眼看到它做了什么**，所以这里放开。
 *
 * 干两件事：
 *   1. 展开工具时间线。产品里是 `<details open={isStreaming}>`，重开历史会话时
 *      `isStreaming=false` 所以收起，只留一行「已执行 N 步工具调用」。
 *   2. 自动打开产物面板。
 *
 * 两件都要用 MutationObserver 盯着 —— React 会重渲染，一次性设置会被覆盖回去。
 */
(function () {
  "use strict";

  var TOOL_DETAILS = 'details[class*="group/activity"]';
  var PANEL_BUTTON = 'button[title="打开工作区面板"]';
  // 侧栏里每条会话是一个 SidebarMenuButton,渲染成 <li class="group/thread-row"> 里的按钮。
  var THREAD_BUTTON = 'li[class*="group/thread-row"] button';
  var panelOpened = false;
  var threadOpened = false;

  function openOnlyThread() {
    if (threadOpened) return;
    var buttons = document.querySelectorAll(THREAD_BUTTON);
    if (!buttons.length) return;
    // 展示站点只放一条会话:直接进去,省掉"先看到空状态再点一下"这一步。
    threadOpened = true;
    buttons[0].click();
  }

  function expandToolTimelines(root) {
    var nodes = (root || document).querySelectorAll(TOOL_DETAILS);
    for (var i = 0; i < nodes.length; i += 1) {
      // 用户手动收起过就别再强行展开，否则点不动。
      if (!nodes[i].open && nodes[i].dataset.demoCollapsed !== "1") {
        nodes[i].open = true;
      }
    }
  }

  function rememberManualCollapse(event) {
    var node = event.target;
    if (node && node.matches && node.matches(TOOL_DETAILS)) {
      node.dataset.demoCollapsed = node.open ? "0" : "1";
    }
  }

  function openArtifactPanel() {
    if (panelOpened) return;
    var button = document.querySelector(PANEL_BUTTON);
    if (!button) return;
    panelOpened = true;
    button.click();
  }

  function tick() {
    openOnlyThread();
    expandToolTimelines(document);
    openArtifactPanel();
  }

  document.addEventListener("toggle", rememberManualCollapse, true);

  var observer = new MutationObserver(function () {
    // 合并到下一帧，避免每条 DOM 变更都扫一遍。
    if (observer._queued) return;
    observer._queued = true;
    requestAnimationFrame(function () {
      observer._queued = false;
      tick();
    });
  });

  function start() {
    observer.observe(document.body, { childList: true, subtree: true });
    tick();
    // 首屏数据是异步拉的，面板按钮可能还没渲染出来；退避重试几次即可。
    [300, 800, 1600, 3000].forEach(function (delay) { setTimeout(tick, delay); });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
