-- 팬싸 레이더 봇 저장소 (Cloudflare D1)
CREATE TABLE IF NOT EXISTS subs (
  chat_id  TEXT PRIMARY KEY,          -- 텔레그램 채팅 ID
  artists  TEXT NOT NULL DEFAULT '[]', -- 받을 아티스트 (JSON 배열)
  all_on   INTEGER NOT NULL DEFAULT 0, -- 모든 새 공지 받기
  overseas INTEGER NOT NULL DEFAULT 0, -- 해외 팬싸 받기
  joined   TEXT,
  updated  TEXT
);
CREATE TABLE IF NOT EXISTS links (     -- 웹 관심 아티스트 → t.me/봇?start=코드 (30일)
  code     TEXT PRIMARY KEY,
  artists  TEXT NOT NULL,
  expires  INTEGER NOT NULL
);
