/**
 * 展示页专用补丁 —— 只作用于 `demo/build.sh` 产出的静态站点，**不进产品源码**。
 *
 * 静态实录页和真实产品的取舍不一样：真实产品里这些默认值都是对的（不替用户选会话、
 * 收起工具时间线免得刷屏、不自动弹面板免得挡住对话），但展示页的目的就是
 * **让人一按链接就看到它做了什么**，所以这里全部放开。
 *
 * 干四件事：
 *   1. 拉一块遮罩盖住首屏。不然会先闪一下"新建对话"的空状态再跳进会话。
 *   2. 直接进入唯一那条会话。
 *   3. 展开工具时间线。产品里是 `<details open={isStreaming}>`，重开历史会话时
 *      `isStreaming=false` 所以收起，只留一行「已执行 N 步工具调用」。
 *   4. 打开产物面板，并把对话滚到最后一条消息的末尾。
 *
 * 都要用 MutationObserver 盯着 —— React 会重渲染，一次性设置会被覆盖回去。
 */
(function () {
  "use strict";

  var TOOL_DETAILS = 'details[class*="group/activity"]';
  var OPEN_PANEL_BUTTON = 'button[title="打开工作区面板"]';
  // 关闭按钮只在面板**已经打开**时才渲染,拿它当"开没开"的判据。
  // 旧版本用的是一次性布尔标志:点一下就置位,可点击没生效(会话还没加载完、
  // 或切会话把面板状态重置了)就再也不重试,面板于是永远不开。
  var PANEL_IS_OPEN = 'button[title="关闭工作区面板"]';
  // 侧栏里每条会话是一个 SidebarMenuButton,渲染成 <li class="group/thread-row"> 里的按钮。
  var THREAD_BUTTON = 'li[class*="group/thread-row"] button';
  var CURTAIN_ID = "slotflow-demo-curtain";
  // 兜底:无论如何到点必须撤掉遮罩,任何判断失误都不能把页面永久盖成白屏。
  var CURTAIN_MAX_MS = 8000;

  var threadOpened = false;
  var startedAt = Date.now();

  function curtain() {
    return document.getElementById(CURTAIN_ID);
  }

  function raiseCurtain() {
    if (curtain() || !document.body) return;
    var node = document.createElement("div");
    node.id = CURTAIN_ID;
    node.setAttribute("style", [
      "position:fixed", "inset:0", "z-index:2147483647",
      "background:var(--background,#0b0b0c)",
      "transition:opacity .35s ease", "opacity:1", "pointer-events:none",
    ].join(";"));
    document.body.appendChild(node);
  }

  function dropCurtain() {
    var node = curtain();
    if (!node) return;
    node.style.opacity = "0";
    setTimeout(function () {
      if (node.parentNode) node.parentNode.removeChild(node);
    }, 400);
  }

  /** 会话渲染完成的信号:这条展示会话必定带工具时间线。 */
  function conversationReady() {
    return document.querySelectorAll(TOOL_DETAILS).length > 0;
  }

  function openOnlyThread() {
    if (threadOpened) return;
    var buttons = document.querySelectorAll(THREAD_BUTTON);
    if (!buttons.length) return;
    // 展示站点只放一条会话:直接进去,省掉"先看到空状态再点一下"这一步。
    threadOpened = true;
    buttons[0].click();
  }

  function expandToolTimelines() {
    var nodes = document.querySelectorAll(TOOL_DETAILS);
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

  /** 元素是否真的显示出来了(不只是存在于 DOM)。 */
  function isVisible(node) {
    if (!node) return false;
    if (node.offsetParent === null) return false;
    var rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function openArtifactPanel() {
    // 判据必须是"关闭按钮**可见**",不能只看它存不存在:
    // 面板收起时这个按钮**仍然留在 DOM 里**,只是被折叠成 0×0(offsetParent 为 null)。
    // 早先版本用 querySelector 一存在就 return,于是每一轮都提前退出,
    // 打开按钮一次都没被点到,面板永远不开。
    if (isVisible(document.querySelector(PANEL_IS_OPEN))) return;
    var button = document.querySelector(OPEN_PANEL_BUTTON);
    if (isVisible(button)) button.click();
  }

  /** 滚到最后一条消息的末尾。 */
  function scrollToLatest() {
    // 不能按"可滚动高度最大"来挑:工具时间线自己有个 max-h-[30rem] 的内部滚动区,
    // 它的可滚动高度(5.7 万)比消息列表(1 万)还大,挑最大只会把时间线滚到底、
    // 消息列表原地不动。真正的判据是**它装着工具时间线,而自己不在时间线内部**。
    var nodes = document.querySelectorAll("div");
    for (var i = 0; i < nodes.length; i += 1) {
      var node = nodes[i];
      var overflow = getComputedStyle(node).overflowY;
      if (overflow !== "auto" && overflow !== "scroll") continue;
      if (node.scrollHeight - node.clientHeight < 200) continue;
      if (node.closest("details")) continue;                        // 时间线内部的滚动区
      if (!node.querySelector(TOOL_DETAILS)) continue;              // 不装消息的滚动区
      node.scrollTop = node.scrollHeight;
      return;
    }
  }

  function tick() {
    openOnlyThread();
    expandToolTimelines();
    if (conversationReady()) {
      openArtifactPanel();
      scrollToLatest();
      dropCurtain();
    } else if (Date.now() - startedAt > CURTAIN_MAX_MS) {
      dropCurtain();
    }
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
    raiseCurtain();
    observer.observe(document.body, { childList: true, subtree: true });
    tick();
    // 首屏数据是异步拉的，会话/面板按钮可能还没渲染出来；退避重试几次。
    // 末尾几次也负责在内容涨完之后把滚动位置再压到底。
    [150, 400, 800, 1600, 2600, 4000, 6000].forEach(function (delay) {
      setTimeout(tick, delay);
    });
    setTimeout(dropCurtain, CURTAIN_MAX_MS);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
