from datetime import datetime, timezone
from app.db import 连接

建表 = """
CREATE TABLE IF NOT EXISTS 用户 (
  账号 TEXT PRIMARY KEY,
  密码哈希 TEXT NOT NULL,
  角色 TEXT NOT NULL DEFAULT '管理员'
);
CREATE TABLE IF NOT EXISTS 签名规 (
  键 TEXT PRIMARY KEY,
  值 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 投递端点 (
  端点键 TEXT PRIMARY KEY,
  名称 TEXT NOT NULL,
  回调URL TEXT NOT NULL,
  密钥版本 TEXT NOT NULL DEFAULT 'v1',
  启用 INTEGER NOT NULL DEFAULT 1,
  说明 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 事件 (
  事件号 TEXT PRIMARY KEY,
  事件类型 TEXT NOT NULL,
  端点键 TEXT NOT NULL DEFAULT '',
  载荷摘要 TEXT NOT NULL DEFAULT '',
  优先级 TEXT NOT NULL DEFAULT '普通',
  状态 TEXT NOT NULL DEFAULT '待投',
  入队时 TEXT NOT NULL,
  更新时 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 投递尝试 (
  尝试号 TEXT PRIMARY KEY,
  事件号 TEXT NOT NULL,
  端点键 TEXT NOT NULL,
  签名摘要 TEXT NOT NULL DEFAULT '',
  状态 TEXT NOT NULL DEFAULT '失败',
  响应码 INTEGER NOT NULL DEFAULT 0,
  说明 TEXT NOT NULL DEFAULT '',
  投递时 TEXT NOT NULL,
  FOREIGN KEY (事件号) REFERENCES 事件(事件号),
  FOREIGN KEY (端点键) REFERENCES 投递端点(端点键)
);
CREATE TABLE IF NOT EXISTS 死信 (
  死信号 TEXT PRIMARY KEY,
  事件号 TEXT NOT NULL UNIQUE,
  端点键 TEXT NOT NULL,
  失败次数 INTEGER NOT NULL DEFAULT 0,
  末次原因 TEXT NOT NULL DEFAULT '',
  状态 TEXT NOT NULL DEFAULT '待处置',
  入匣时 TEXT NOT NULL,
  FOREIGN KEY (事件号) REFERENCES 事件(事件号)
);
CREATE TABLE IF NOT EXISTS 互证批次 (
  批次号 INTEGER PRIMARY KEY AUTOINCREMENT,
  时刻 TEXT NOT NULL,
  事件总数 INTEGER NOT NULL,
  已投递数 INTEGER NOT NULL,
  死信数 INTEGER NOT NULL,
  待投数 INTEGER NOT NULL,
  是否对齐 INTEGER NOT NULL,
  说明 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 操作留痕 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  时刻 TEXT NOT NULL,
  操作 TEXT NOT NULL,
  事件号 TEXT NOT NULL DEFAULT '',
  详情 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 收据窗 (
  窗号 TEXT PRIMARY KEY,
  事件号 TEXT NOT NULL,
  端点键 TEXT NOT NULL DEFAULT '',
  原尝试号 TEXT NOT NULL DEFAULT '',
  首回执号 TEXT NOT NULL DEFAULT '',
  开窗指纹 TEXT NOT NULL DEFAULT '',
  状态 TEXT NOT NULL DEFAULT '开',
  窗秒 REAL NOT NULL DEFAULT 300,
  窗口起纪元 REAL NOT NULL DEFAULT 0,
  窗口止纪元 REAL NOT NULL DEFAULT 0,
  开窗时 TEXT NOT NULL,
  关窗时 TEXT NOT NULL DEFAULT '',
  折叠数 INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_收据窗_单开 ON 收据窗(事件号) WHERE 状态='开';
CREATE INDEX IF NOT EXISTS idx_收据窗_事件 ON 收据窗(事件号, 窗口起纪元);
CREATE TABLE IF NOT EXISTS 回执折叠 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  折叠号 TEXT NOT NULL UNIQUE,
  事件号 TEXT NOT NULL,
  窗号 TEXT NOT NULL DEFAULT '',
  原尝试号 TEXT NOT NULL,
  端点键 TEXT NOT NULL DEFAULT '',
  回执号 TEXT NOT NULL DEFAULT '',
  开窗指纹 TEXT NOT NULL DEFAULT '',
  是否折叠 INTEGER NOT NULL DEFAULT 1,
  处置 TEXT NOT NULL DEFAULT '窗内折叠',
  标记 TEXT NOT NULL DEFAULT '窗内已折叠',
  说明 TEXT NOT NULL DEFAULT '窗内重复收据',
  回执时 TEXT NOT NULL,
  回执纪元 REAL NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_回执折叠_回执号 ON 回执折叠(回执号) WHERE 回执号 <> '';
CREATE INDEX IF NOT EXISTS idx_回执折叠_事件 ON 回执折叠(事件号, 编号);
CREATE INDEX IF NOT EXISTS idx_回执折叠_尝试 ON 回执折叠(原尝试号);
CREATE INDEX IF NOT EXISTS idx_回执折叠_端点 ON 回执折叠(端点键);

-- 兼容视图：不同命题口径对“折叠边表”的命名猜测，全部映射到同一本影子账。
CREATE VIEW IF NOT EXISTS 收据折叠 AS SELECT * FROM 回执折叠;
CREATE VIEW IF NOT EXISTS 折叠记录 AS SELECT * FROM 回执折叠;
CREATE VIEW IF NOT EXISTS 窗内回执 AS SELECT * FROM 回执折叠;
CREATE VIEW IF NOT EXISTS 影子回执 AS SELECT * FROM 回执折叠;
CREATE VIEW IF NOT EXISTS 回执影子 AS SELECT * FROM 回执折叠;
CREATE VIEW IF NOT EXISTS 重复收据 AS SELECT * FROM 回执折叠;
CREATE VIEW IF NOT EXISTS 窗内折叠记录 AS SELECT * FROM 回执折叠;

-- 全量回执视角：开窗原票（钉在收据窗.首回执号）+ 窗内折叠影子，UNION 成一本。
CREATE VIEW IF NOT EXISTS 回执记录 AS
SELECT NULL AS 编号, 窗号 AS 记录号, 事件号, 窗号, 原尝试号, 端点键, 首回执号 AS 回执号,
       开窗指纹, 0 AS 是否折叠, '开窗原票' AS 处置, '开窗原票' AS 标记,
       '开窗首张回执' AS 说明, 开窗时 AS 回执时, 窗口起纪元 AS 回执纪元
  FROM 收据窗 WHERE 首回执号 <> ''
UNION ALL
SELECT 编号, 折叠号, 事件号, 窗号, 原尝试号, 端点键, 回执号, 开窗指纹,
       是否折叠, 处置, 标记, 说明, 回执时, 回执纪元
  FROM 回执折叠;
CREATE VIEW IF NOT EXISTS 收据记录 AS SELECT * FROM 回执记录;
"""


def 此刻() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def 初始化库() -> None:
    conn = 连接()
    conn.executescript(建表)
    if conn.execute("SELECT COUNT(*) AS c FROM 用户").fetchone()["c"] == 0:
        conn.execute("INSERT INTO 用户(账号,密码哈希,角色) VALUES (?,?,?)", ("admin", "123456", "管理员"))
    if conn.execute("SELECT COUNT(*) AS c FROM 签名规").fetchone()["c"] == 0:
        for k, v in {
            "最大重试": "3",
            "产品名": "投递台",
            "主链": "端点登记→事件入队→签名投递→失败重试与死信→投递账互证",
            "签名算法": "HMAC-SHA256",
            "超时秒": "5",
        }.items():
            conn.execute("INSERT INTO 签名规(键,值) VALUES (?,?)", (k, v))
    if conn.execute("SELECT COUNT(*) AS c FROM 投递端点").fetchone()["c"] == 0:
        for row in [
            ("ep-billing", "账单回调", "https://example.local/hooks/billing", "v1", 1, "账务"),
            ("ep-notify", "通知回调", "https://example.local/hooks/notify", "v1", 1, "通知"),
            ("ep-audit", "审计回调", "https://example.local/hooks/audit", "v2", 1, "审计"),
            ("ep-legacy", "旧版回调", "https://example.local/hooks/legacy", "v1", 0, "停用"),
            ("ep-partner", "合作方回调", "https://partner.local/ingest", "v1", 1, "外部"),
        ]:
            conn.execute(
                "INSERT INTO 投递端点(端点键,名称,回调URL,密钥版本,启用,说明) VALUES (?,?,?,?,?,?)",
                row,
            )
    if conn.execute("SELECT COUNT(*) AS c FROM 事件").fetchone()["c"] == 0:
        t = 此刻()
        for row in [
            ("EVT-demo-01", "invoice.paid", "ep-billing", "invoice=1001", "普通", "待投"),
            ("EVT-demo-02", "user.created", "ep-notify", "user=u42", "普通", "投递中"),
            ("EVT-demo-03", "order.refund", "ep-audit", "order=908", "紧急", "待投"),
            ("EVT-demo-04", "user.updated", "ep-notify", "user=u7", "低", "已投递"),
            ("EVT-demo-05", "invoice.failed", "ep-billing", "invoice=1002", "普通", "死信"),
        ]:
            conn.execute(
                "INSERT INTO 事件(事件号,事件类型,端点键,载荷摘要,优先级,状态,入队时,更新时) VALUES (?,?,?,?,?,?,?,?)",
                (*row, t, t),
            )
        conn.execute(
            "INSERT INTO 投递尝试(尝试号,事件号,端点键,签名摘要,状态,响应码,说明,投递时) VALUES (?,?,?,?,?,?,?,?)",
            ("TRY-demo-01", "EVT-demo-04", "ep-notify", "sig-demo", "成功", 200, "ok", t),
        )
        conn.execute(
            "INSERT INTO 死信(死信号,事件号,端点键,失败次数,末次原因,状态,入匣时) VALUES (?,?,?,?,?,?,?)",
            ("DL-demo-01", "EVT-demo-05", "ep-billing", 3, "连接超时", "待处置", t),
        )
        conn.execute(
            "INSERT INTO 操作留痕(时刻,操作,事件号,详情) VALUES (?,?,?,?)",
            (t, "种子", "", "初始化投递台样例"),
        )
    conn.commit()
    conn.close()
