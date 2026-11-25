// 옛 주소(fansign-radar.fansign-radar.workers.dev) → 새 주소로 넘기기
export default {
  fetch(req) {
    const u = new URL(req.url);
    return Response.redirect(`https://fansign-radar.pages.dev${u.pathname}${u.search}${u.hash}`, 301);
  },
};
