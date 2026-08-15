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
 *
 * **但盯归盯，只盯到用户接手为止。** 这条比"盯着"本身更要紧：补丁的职责是摆好首屏，
 * 不是替用户一直操作页面。早先版本每次 DOM 变动都无条件重跑"开面板 + 压到底"，
 * 结果是**面板折叠不了**（一收起 → DOM 变 → 下一帧又给点开）、**消息跳转用不了**
 * （产品用的是 `behavior:"smooth"`，几十帧的平滑滚动被逐帧掐断，跳过去立刻弹回底部）。
 * 所以两件事各有各的收手判据 —— 见 openArtifactPanel / tick。
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
  // 会话就绪之后还盯这么久的滚动位置:图片、代码块、面板挤占宽度都会把内容高度撑大,
  // 压一次不够。过了这个窗口就撒手,剩下的交给产品自己的粘底逻辑。
  var AUTO_SCROLL_SETTLE_MS = 2500;

  var threadOpened = false;
  // 是否**观察到**面板真的开过一次(不是"点过一次")。见 openArtifactPanel 的注释。
  var panelOpened = false;
  // 用户有过真实手势 —— 从此不再碰滚动位置。
  var userTookOver = false;
  var startedAt = Date.now();
  var readyAt = 0;

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
    if (isVisible(document.querySelector(PANEL_IS_OPEN))) {
      // 关键区别:置位的条件是"**观察到**它开了",而不是"我点过了"。
      //   · 按"点过了"置位 —— 点击可能没生效(会话还没加载完),一次性标志一置就再不重试,
      //     面板永远不开;
      //   · 完全不置位(旧版) —— 用户一折叠,关闭按钮变不可见,下一次 DOM 变动又给点开,
      //     面板成了**关不掉的**。
      // 观察到开过一次就撒手,两头都对:重试保留到真正生效为止,生效之后控制权归用户。
      panelOpened = true;
      return;
    }
    if (panelOpened) return;
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
      if (!readyAt) readyAt = Date.now();
      openArtifactPanel();
      // 压到底只在"刚进页面"那一小段窗口里做。过了窗口、或用户有过任何手势,就彻底撒手:
      // 产品的消息跳转用的是 `behavior:"smooth"`(平滑滚动要跑几十帧),每帧都被这里
      // 改写 scrollTop 的话,跳过去会立刻弹回底部 —— 跳转按钮等于废掉。
      if (!userTookOver && Date.now() - readyAt < AUTO_SCROLL_SETTLE_MS) {
        scrollToLatest();
      }
      dropCurtain();
    } else if (Date.now() - startedAt > CURTAIN_MAX_MS) {
      dropCurtain();
    }
  }

  /**
   * 用户接手的信号 —— 只认**真实手势**。
   *
   * `element.click()` 派发的是 isTrusted=false 的合成事件,所以我们自己点会话、点开面板
   * 不会误判成"用户接手"把自己锁死。这和产品里 useStickToBottom 的判据是同一套思路:
   * 程序滚动不产生 wheel/touch/键盘/指针手势。
   */
  function markUserTakeover(event) {
    if (event.isTrusted) userTookOver = true;
  }

  document.addEventListener("toggle", rememberManualCollapse, true);
  ["pointerdown", "wheel", "touchstart", "keydown"].forEach(function (type) {
    document.addEventListener(type, markUserTakeover, { capture: true, passive: true });
  });

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
    // 靠后那几次只为"面板一直没开出来"兜底 —— 滚动早被 AUTO_SCROLL_SETTLE_MS 关掉了。
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
