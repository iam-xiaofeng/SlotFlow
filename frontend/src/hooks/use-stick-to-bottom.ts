import { useCallback, useEffect, useRef } from "react";

/**
 * 「粘底滚动」：内容增长时自动跟到底部，用户往上滚就停住，滚回底部又恢复跟随。
 *
 * 聊天主列表和思考框是同一套语义，但它们不共享一个滚动容器——思考框是消息气泡内部的
 * `max-h + overflow-y-auto` 小盒子，内容流式增长时完全没有自动滚动，用户得手动往下拖才能
 * 看到最新一步。这个 hook 把这段行为抽出来给它用。
 *
 * 判据刻意**不去区分「谁触发了这次滚动」**（时间窗那套在流式期间会被每批 token 不断续期，
 * 结果把用户的滚动一起吞掉，页面被钉死在底部）。只看两件事：
 *   · 当前在底部            → 跟随；
 *   · 不在底部 + 有真实手势 → 停住。
 * 程序滚动不产生 wheel/touch/键盘手势，所以不会误判成用户意图。
 */
export function useStickToBottom<T extends HTMLElement>(
  /** 内容变化的标识（例如流式文本长度）；变化时尝试跟随到底部。 */
  contentKey: string | number | null,
  options: { enabled?: boolean; thresholdPx?: number } = {},
) {
  const { enabled = true, thresholdPx = 24 } = options;
  const ref = useRef<T | null>(null);
  const followRef = useRef(true);
  const userIntentRef = useRef(false);

  const isNearBottom = useCallback(
    (element: HTMLElement) => {
      const max = Math.max(0, element.scrollHeight - element.clientHeight);
      return max - element.scrollTop <= thresholdPx;
    },
    [thresholdPx],
  );

  useEffect(() => {
    const element = ref.current;
    if (!element) {
      return;
    }

    const markIntent = () => {
      userIntentRef.current = true;
    };
    const handleScroll = () => {
      if (isNearBottom(element)) {
        followRef.current = true;
        userIntentRef.current = false;
        return;
      }
      if (userIntentRef.current) {
        followRef.current = false;
      }
    };

    element.addEventListener("scroll", handleScroll, { passive: true });
    element.addEventListener("wheel", markIntent, { passive: true });
    element.addEventListener("touchmove", markIntent, { passive: true });
    element.addEventListener("pointerdown", markIntent, { passive: true });
    element.addEventListener("keydown", markIntent);
    return () => {
      element.removeEventListener("scroll", handleScroll);
      element.removeEventListener("wheel", markIntent);
      element.removeEventListener("touchmove", markIntent);
      element.removeEventListener("pointerdown", markIntent);
      element.removeEventListener("keydown", markIntent);
    };
  }, [isNearBottom]);

  useEffect(() => {
    if (!enabled || contentKey === null || !followRef.current) {
      return;
    }
    const element = ref.current;
    if (!element) {
      return;
    }
    const frame = window.requestAnimationFrame(() => {
      const current = ref.current;
      if (current && followRef.current) {
        current.scrollTop = current.scrollHeight;
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, [contentKey, enabled]);

  return ref;
}
