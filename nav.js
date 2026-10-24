(function () {
  // 表示中のタブをもう一度タップ → 先頭へスクロール（iOSの標準的な挙動）
  var cur = document.querySelector('.tabbar .tab[aria-current="page"]');
  if (cur) cur.addEventListener("click", function (e) {
    e.preventDefault();
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
  // どちらの画面から開いても Service Worker を有効化（両画面をオフライン対応に）
  if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost")) {
    navigator.serviceWorker.register("sw.js").catch(function () {});
  }
})();
