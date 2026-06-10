import sqlite3
import json
import threading
from pathlib import Path
import logging
from core.config_manager import RULE_ARRAY_FIELDS

logger = logging.getLogger(__name__)

class DatabaseManager:
    # 完整的 Migration SQL，不使用省略号
    MIGRATIONS = [
        # Version 1: 初始表结构
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS node_groups (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            sort_order INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS subscriptions (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            last_update TEXT,
            node_count INTEGER DEFAULT 0,
            auto_update INTEGER DEFAULT 0 CHECK (auto_update IN (0, 1)),
            update_interval INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS nodes (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            protocol TEXT NOT NULL CHECK (protocol IN ('vmess', 'vless', 'trojan', 'shadowsocks', 'hysteria2', 'wireguard')),
            address TEXT NOT NULL,
            port INTEGER NOT NULL CHECK (port > 0 AND port <= 65535),
            config TEXT NOT NULL,
            tag TEXT NOT NULL UNIQUE,
            group_id TEXT,
            subscription_id TEXT,
            is_enabled INTEGER DEFAULT 1 CHECK (is_enabled IN (0, 1)),
            latency INTEGER,
            speed INTEGER,
            last_test_at TEXT,
            sort_order INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (group_id) REFERENCES node_groups(id) ON DELETE SET NULL,
            FOREIGN KEY (subscription_id) REFERENCES subscriptions(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_nodes_group ON nodes(group_id);
        CREATE INDEX IF NOT EXISTS idx_nodes_subscription ON nodes(subscription_id);
        CREATE INDEX IF NOT EXISTS idx_nodes_protocol ON nodes(protocol);
        CREATE INDEX IF NOT EXISTS idx_nodes_enabled ON nodes(is_enabled);
        CREATE TABLE IF NOT EXISTS rule_sets (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            tag TEXT NOT NULL UNIQUE,
            type TEXT NOT NULL DEFAULT 'remote' CHECK (type IN ('local', 'remote')),
            format TEXT NOT NULL DEFAULT 'binary' CHECK (format IN ('binary', 'source')),
            url TEXT,
            download_detour TEXT DEFAULT 'proxy',
            is_enabled INTEGER DEFAULT 1 CHECK (is_enabled IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS routing_rules (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            outbound_tag TEXT NOT NULL,
            domain TEXT,
            domain_suffix TEXT,
            domain_keyword TEXT,
            domain_regex TEXT,
            geosite TEXT,
            ip_cidr TEXT,
            ip_is_private INTEGER,
            geoip TEXT,
            source_ip_cidr TEXT,
            source_geoip TEXT,
            port TEXT,
            port_range TEXT,
            source_port TEXT,
            source_port_range TEXT,
            process_name TEXT,
            process_path TEXT,
            package_name TEXT,
            network TEXT,
            protocol TEXT,
            user_id TEXT,
            clash_mode TEXT,
            invert INTEGER DEFAULT 0,
            rule_set_id TEXT,
            is_enabled INTEGER DEFAULT 1 CHECK (is_enabled IN (0, 1)),
            sort_order INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (rule_set_id) REFERENCES rule_sets(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_rules_outbound ON routing_rules(outbound_tag);
        CREATE INDEX IF NOT EXISTS idx_rules_enabled ON routing_rules(is_enabled);
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """,
        # 后续迁移在此添加...
    ]

    def __init__(self):
        self.db_path = Path.home() / ".venlta" / "venlta.db"
        # 确保数据库目录存在（SQLite 不会自动创建父目录）
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # 线程安全：使用 threading.local 为每个线程创建独立连接
        self._local = threading.local()
        self._lock = threading.Lock()  # 保护 Migration 等写操作
        # 实例级连接追踪（替代类级可变默认值，避免多实例共享同一列表）
        self._all_connections = []
        self._all_connections_lock = threading.Lock()
        self._shutdown = False  # close_all() 后设为 True，阻止 _get_conn() 返回新连接
        # 主连接用于初始化和 Migration
        self._init_conn()

    def _get_conn(self) -> sqlite3.Connection:
        """获取当前线程的数据库连接（线程安全）"""
        if self._shutdown:
            raise RuntimeError("DatabaseManager has been shut down, no new connections allowed")
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            conn = sqlite3.connect(str(self.db_path))
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
            # 注册到全局连接追踪列表，供 close_all 遍历关闭
            with self._all_connections_lock:
                self._all_connections.append(conn)
        return self._local.conn

    def _init_conn(self):
        """初始化连接（主线程用）"""
        self._local.conn = None
        conn = self._get_conn()
        return conn

    def migrate(self):
        with self._lock:  # Migration 需要全局锁保护
            current = self._get_schema_version()
            for i, sql in enumerate(self.MIGRATIONS[current:], start=current + 1):
                conn = self._get_conn()
                try:
                    # executescript 正确处理多语句 SQL（包括分号在字符串中的情况）
                    # 注意：SQLite DDL 无法回滚，但 IF NOT EXISTS 保证幂等
                    conn.executescript(sql)
                    conn.execute(
                        "INSERT INTO schema_version (version, applied_at) VALUES (?, datetime('now'))",
                        (i,)
                    )
                    conn.commit()
                    logger.info(f"Database migrated to version {i}")
                except Exception as e:
                    logger.error(f"Migration to version {i} failed: {e}")
                    raise

    def _get_schema_version(self) -> int:
        try:
            result = self._get_conn().execute("SELECT MAX(version) FROM schema_version").fetchone()
            return result[0] if result[0] is not None else 0
        except sqlite3.OperationalError:
            return 0

    # ---------- 通用辅助 ----------
    # 表名白名单，防止 SQL 注入
    ALLOWED_TABLES = {'node_groups', 'nodes', 'subscriptions', 'routing_rules', 'rule_sets', 'app_settings'}
    # 列名白名单，防止 SQL 注入
    NODE_GROUP_COLUMNS = {'name', 'sort_order'}
    NODE_COLUMNS = {'name', 'protocol', 'address', 'port', 'config', 'tag', 'group_id', 'subscription_id', 'is_enabled', 'sort_order', 'latency', 'speed', 'last_test_at'}
    RULE_COLUMNS = {'name', 'outbound_tag', 'domain', 'domain_suffix', 'domain_keyword', 'domain_regex', 'geosite', 'ip_cidr', 'ip_is_private', 'geoip', 'source_ip_cidr', 'source_geoip', 'port', 'port_range', 'source_port', 'source_port_range', 'process_name', 'process_path', 'package_name', 'network', 'protocol', 'user_id', 'clash_mode', 'invert', 'rule_set_id', 'is_enabled', 'sort_order'}
    RULE_SET_COLUMNS = {'name', 'tag', 'type', 'format', 'url', 'download_detour', 'is_enabled'}
    # 规则中的数组字段，需要 JSON 序列化/反序列化（类常量避免重复定义）
    # 引用模块级常量（config_manager.py 中定义），避免跨类/跨模块重复定义
    # 注意：此行 RHS 的 RULE_ARRAY_FIELDS 是从 core.config_manager 导入的模块级常量，
    # 不是类自身的属性（Python 类体赋值语句的 RHS 在类命名空间创建之前求值）。
    # 为避免混淆，可改用别名：from core.config_manager import RULE_ARRAY_FIELDS as _RULE_ARRAY_FIELDS
    _RULE_ARRAY_FIELDS = RULE_ARRAY_FIELDS  # 别名避免自引用困惑

    def _safe_update(self, table: str, allowed_cols: set, id_val: str, updates: dict):
        """安全的 UPDATE 方法，只允许白名单表名和列名
        
        防双重转换守卫：如果 updates 的键已全部是 snake_case（即全部属于 allowed_cols 集合），
        说明调用方已通过 _convert_keys 完成转换，无需再次转换。
        此守卫防止 CRUD 方法先调用 _convert_keys 转换 camelCase→snake_case，
        然后 _safe_update 再次调用 _convert_keys 导致双重转换（如 lastUpdate → last_update → last_update 无变化但浪费计算）。
        """
        if table not in self.ALLOWED_TABLES:
            raise ValueError(f"Invalid table name: {table}")
        # 防双重转换守卫：如果所有键都已在 allowed_cols 中，说明已经是 snake_case，跳过重新过滤
        # 仅过滤掉不在白名单中的键（如前端传入的 id 等非更新字段）
        filtered = {k: v for k, v in updates.items() if k in allowed_cols}
        if not filtered:
            return
        sets = ", ".join(f"{k} = ?" for k in filtered.keys())
        # 使用同一个 conn 对象执行 execute + commit，避免 _get_conn() 两次调用
        # （虽然 threading.local 保证同线程返回同一连接，但显式复用更清晰且消除理论风险）
        conn = self._get_conn()
        cursor = conn.execute(
            f"UPDATE {table} SET {sets}, updated_at = datetime('now') WHERE id = ?",
            (*filtered.values(), id_val)
        )
        conn.commit()
        # 检查受影响行数，id 不存在时记录警告（不抛异常，保持向后兼容）
        if cursor.rowcount == 0:
            logger.warning(f"_safe_update: {table} id={id_val} not found, no rows updated")

    # ---------- 分组 CRUD ----------
    def get_node_groups(self) -> list:
        cursor = self._get_conn().execute("SELECT * FROM node_groups ORDER BY sort_order")
        rows = [dict(zip([d[0] for d in cursor.description], row)) for row in cursor.fetchall()]
        # snake_case → camelCase 转换
        return [self._convert_keys(r, self.GROUP_KEY_MAP) for r in rows]

    def add_node_group(self, data: dict) -> str:
        import uuid
        group_id = data.get('id', str(uuid.uuid4()))
        self._get_conn().execute(
            "INSERT INTO node_groups (id, name, sort_order) VALUES (?, ?, ?)",
            (group_id, data['name'], data.get('sort_order', 0))
        )
        self._get_conn().commit()
        return group_id

    def update_node_group(self, group_id: str, updates: dict):
        # 前端可能传入 camelCase 键名，自动转换为 snake_case
        updates = self._convert_keys(updates, self.GROUP_KEY_MAP_REVERSE)
        self._safe_update("node_groups", self.NODE_GROUP_COLUMNS, group_id, updates)

    def delete_node_group(self, group_id: str):
        # 删除分组时，组内节点的 group_id 会被 SET NULL（外键约束）
        self._get_conn().execute("DELETE FROM node_groups WHERE id = ?", (group_id,))
        self._get_conn().commit()

    # ---------- 节点 CRUD ----------
    # 数据库 snake_case → 前端 camelCase 字段映射
    NODE_KEY_MAP = {
        'group_id': 'groupId',
        'subscription_id': 'subscriptionId',
        'is_enabled': 'isEnabled',
        'sort_order': 'sortOrder',
        'speed': 'speed',          # 显式列出 speed，虽然 snake_case→snake_case 无转换，但确保完整性
        'last_test_at': 'lastTestAt',
        'created_at': 'createdAt',
        'updated_at': 'updatedAt',
    }
    # 反向映射：前端写操作时 camelCase → 数据库 snake_case
    NODE_KEY_MAP_REVERSE = {v: k for k, v in NODE_KEY_MAP.items()}
    RULE_KEY_MAP = {
        'outbound_tag': 'outboundTag',
        'domain_suffix': 'domainSuffix',
        'domain_keyword': 'domainKeyword',
        'domain_regex': 'domainRegex',
        'ip_is_private': 'ipIsPrivate',
        'source_ip_cidr': 'sourceIpCidr',
        'source_geoip': 'sourceGeoip',
        'port_range': 'portRange',
        'source_port': 'sourcePort',
        'source_port_range': 'sourcePortRange',
        'process_name': 'processName',
        'process_path': 'processPath',
        'package_name': 'packageName',
        'user_id': 'userId',
        'clash_mode': 'clashMode',
        'rule_set_id': 'ruleSetId',
        'is_enabled': 'isEnabled',
        'sort_order': 'sortOrder',
        'created_at': 'createdAt',
        'updated_at': 'updatedAt',
    }
    # 反向映射：前端写操作时 camelCase → 数据库 snake_case
    RULE_KEY_MAP_REVERSE = {v: k for k, v in RULE_KEY_MAP.items()}
    GROUP_KEY_MAP = {
        'sort_order': 'sortOrder',
        'created_at': 'createdAt',
        'updated_at': 'updatedAt',
    }
    # 反向映射：前端写操作时 camelCase → 数据库 snake_case
    GROUP_KEY_MAP_REVERSE = {v: k for k, v in GROUP_KEY_MAP.items()}

    # 布尔字段列表：SQLite 存储为 INTEGER (0/1)，需转换为 Python bool 以匹配前端 TypeScript boolean 类型
    # 注意：此列表仅包含 nodes/routing_rules/node_groups/subscriptions/rule_sets 表中的布尔列。
    # auto_update, system_proxy_enabled, tun_enabled 等是 app_settings 的键，
    # 通过 get_setting/update_setting 存取，JSON 序列化天然保持 bool 类型，无需在此列出。
    BOOLEAN_FIELDS = {
        'is_enabled', 'ip_is_private', 'invert',
    }

    def _convert_keys(self, d: dict, key_map: dict) -> dict:
        """将 dict 的 key 从 snake_case 转换为 camelCase（仅转换映射表中的 key）
        
        同时将布尔字段从 SQLite 的 0/1 转换为 Python bool，
        确保前端收到的值与 TypeScript boolean 类型一致（避免 0 !== false 的问题）。
        """
        result = {}
        for k, v in d.items():
            new_key = key_map.get(k, k)
            # 布尔字段值转换：0/1 → False/True（无论键名是否被映射）
            if k in self.BOOLEAN_FIELDS and v is not None:
                v = bool(v)
            result[new_key] = v
        return result

    def _encrypt_value(self, plaintext: str) -> str:
        """加密敏感值（节点配置、订阅链接等），存储前调用"""
        try:
            from utils.crypto import encrypt
            return encrypt(plaintext)
        except Exception:
            logger.warning("Failed to encrypt value, storing as plaintext")
            return plaintext  # 加密失败时降级为明文存储
   
    def _decrypt_value(self, ciphertext: str) -> str:
        """解密敏感值，读取后调用。兼容未加密的旧数据"""
        try:
            from utils.crypto import decrypt
            return decrypt(ciphertext)
        except Exception:
            return ciphertext  # 解密失败（可能是未加密的旧数据），原样返回

    def _parse_config_field(self, value: str) -> dict:
        """解析 config JSON 字段：先尝试解密后解析，失败则尝试直接解析，再失败返回空字典
        
        此方法提取自原 _deserialize_node_rows 和 get_all_nodes_raw 中的重复逻辑，
        统一 config 字段的 解密 → JSON解析 → 降级 处理流程。
        """
        try:
            return json.loads(self._decrypt_value(value))
        except (json.JSONDecodeError, TypeError):
            try:
                return json.loads(value)  # 兼容未加密的旧数据
            except (json.JSONDecodeError, TypeError):
                return {}

    def _deserialize_node_rows(self, cursor) -> list:
        """Deserialize node rows, parsing the config JSON field + snake_case → camelCase 转换"""
        rows = cursor.fetchall()
        result = []
        for row in rows:
            d = dict(zip([desc[0] for desc in cursor.description], row))
            # Parse config JSON string back to dict
            if 'config' in d and isinstance(d['config'], str):
                d['config'] = self._parse_config_field(d['config'])
            # snake_case → camelCase 转换，确保前端字段名一致
            d = self._convert_keys(d, self.NODE_KEY_MAP)
            result.append(d)
        return result

    def get_nodes(self, group_id: str | None = None) -> list:
        if group_id:
            cursor = self._get_conn().execute("SELECT * FROM nodes WHERE group_id = ? ORDER BY sort_order", (group_id,))
        else:
            cursor = self._get_conn().execute("SELECT * FROM nodes ORDER BY sort_order")
        return self._deserialize_node_rows(cursor)

    def get_node_by_tag(self, tag: str) -> dict | None:
        """根据 tag 查找节点（供 batchUpdateNodeLatency 等需要 tag→id 映射的场景使用）"""
        cursor = self._get_conn().execute("SELECT * FROM nodes WHERE tag = ?", (tag,))
        rows = self._deserialize_node_rows(cursor)
        return rows[0] if rows else None

    def get_node_ids_by_tags(self, tags: list[str]) -> dict[str, str]:
        """批量获取 tag → id 映射（供 batchUpdateNodeLatency 使用，避免 N+1 查询）
        
        返回值：{tag: id} 字典，未找到的 tag 不会出现在返回值中。
        相比逐个调用 get_node_by_tag()，此方法只需 1 次 SQL 查询。
        """
        if not tags:
            return {}
        placeholders = ", ".join("?" * len(tags))
        cursor = self._get_conn().execute(
            f"SELECT tag, id FROM nodes WHERE tag IN ({placeholders})",
            tags
        )
        return {row[0]: row[1] for row in cursor.fetchall()}

    def get_all_nodes_raw(self) -> list:
        """获取所有节点（保留数据库原始 snake_case 键名，供内部 _build_config 使用）
        
        注意：get_all_nodes() 返回 camelCase 键（经 _convert_keys 转换），
        但后端 _build_config / _build_outbounds / _build_route 等方法
        需要使用数据库原始列名（snake_case）来匹配字段，
        因此必须使用此 raw 方法，否则 is_enabled→isEnabled 等字段将无法匹配。
        """
        cursor = self._get_conn().execute("SELECT * FROM nodes ORDER BY sort_order")
        rows = cursor.fetchall()
        result = []
        for row in rows:
            d = dict(zip([desc[0] for desc in cursor.description], row))
            if 'config' in d and isinstance(d['config'], str):
                d['config'] = self._parse_config_field(d['config'])
            result.append(d)
        return result

    def add_node(self, data: dict) -> str:
        import uuid
        # 前端可能传入 camelCase 键名，自动转换为 snake_case
        data = self._convert_keys(data, self.NODE_KEY_MAP_REVERSE)
        node_id = data.get('id', str(uuid.uuid4()))
        # 校验 tag 唯一性，避免 INSERT 时 IntegrityError 不友好
        tag = data.get('tag', node_id)
        existing = self._get_conn().execute("SELECT 1 FROM nodes WHERE tag = ?", (tag,)).fetchone()
        if existing:
            raise ValueError(f"Node tag '{tag}' already exists")
        self._get_conn().execute(
            "INSERT INTO nodes (id, name, protocol, address, port, config, tag, group_id, subscription_id, is_enabled, sort_order) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (node_id, data['name'], data['protocol'], data['address'], data['port'],
             self._encrypt_value(json.dumps(data.get('config', {}))), data.get('tag', node_id),
             data.get('group_id'), data.get('subscription_id'), data.get('is_enabled', 1), data.get('sort_order', 0))
        )
        self._get_conn().commit()
        return node_id

    def update_node(self, node_id: str, updates: dict):
        # 前端可能传入 camelCase 键名，自动转换为 snake_case
        updates = self._convert_keys(updates, self.NODE_KEY_MAP_REVERSE)
        if 'config' in updates and isinstance(updates['config'], dict):
            updates['config'] = self._encrypt_value(json.dumps(updates['config']))
        self._safe_update("nodes", self.NODE_COLUMNS, node_id, updates)

    def delete_node(self, node_id: str):
        self._get_conn().execute("DELETE FROM nodes WHERE id = ?", (node_id,))
        self._get_conn().commit()

    def batch_update_node_latency(self, updates: list[dict]):
        """批量更新节点延迟/速度测试结果（单次事务，避免 N 次单独 UPDATE）
        
        参数：updates 为 [{id: str, latency?: int, speed?: int, last_test_at: str}, ...]
        支持延迟和速度两种测试结果的批量更新，字段可选（仅更新提供的字段）。
        使用事务保证原子性：全部成功或全部回滚。
        """
        conn = self._get_conn()
        try:
            for u in updates:
                # 动态构建 SET 子句，仅更新提供的字段
                set_parts = []
                params = []
                if 'latency' in u:
                    set_parts.append("latency = ?")
                    params.append(u['latency'])
                if 'speed' in u:
                    set_parts.append("speed = ?")
                    params.append(u['speed'])
                if 'last_test_at' in u:
                    set_parts.append("last_test_at = ?")
                    params.append(u['last_test_at'])
                if not set_parts:
                    continue
                set_parts.append("updated_at = datetime('now')")
                params.append(u['id'])
                conn.execute(
                    f"UPDATE nodes SET {', '.join(set_parts)} WHERE id = ?",
                    params
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    # ---------- 订阅 CRUD ----------
    SUBSCRIPTION_KEY_MAP = {
        'last_update': 'lastUpdate',
        'node_count': 'nodeCount',
        'auto_update': 'autoUpdate',
        'update_interval': 'updateInterval',
        'created_at': 'createdAt',
        'updated_at': 'updatedAt',
    }
    SUBSCRIPTION_KEY_MAP_REVERSE = {v: k for k, v in SUBSCRIPTION_KEY_MAP.items()}

    def get_subscriptions(self) -> list:
        cursor = self._get_conn().execute("SELECT * FROM subscriptions ORDER BY created_at")
        rows = [dict(zip([d[0] for d in cursor.description], row)) for row in cursor.fetchall()]
        for r in rows:
            if 'url' in r and isinstance(r['url'], str):
                try:
                    r['url'] = self._decrypt_value(r['url'])
                except Exception:
                    pass  # 兼容未加密的旧数据
        return [self._convert_keys(r, self.SUBSCRIPTION_KEY_MAP) for r in rows]

    def add_subscription(self, name: str, url: str) -> str:
        import uuid
        sub_id = str(uuid.uuid4())
        self._get_conn().execute(
            "INSERT INTO subscriptions (id, name, url) VALUES (?, ?, ?)",
            (sub_id, name, self._encrypt_value(url))
        )
        self._get_conn().commit()
        return sub_id

    def update_subscription(self, sub_id: str, updates: dict):
        """更新订阅元数据（如 last_update, node_count）"""
        # 前端可能传入 camelCase 键名，自动转换为 snake_case（与其他 update 方法一致）
        updates = self._convert_keys(updates, self.SUBSCRIPTION_KEY_MAP_REVERSE)
        allowed = {"name", "url", "last_update", "node_count", "auto_update", "update_interval"}
        safe_updates = {k: v for k, v in updates.items() if k in allowed}
        if not safe_updates:
            return
        # URL 字段需加密存储（与 add_subscription 保持一致）
        if "url" in safe_updates:
            safe_updates["url"] = self._encrypt_value(safe_updates["url"])
        self._safe_update("subscriptions", allowed, sub_id, safe_updates)

    def delete_subscription(self, sub_id: str):
        # 先删除关联节点，再删除订阅记录（避免节点成为孤儿数据）
        # 注意：不依赖 ON DELETE SET NULL，而是显式删除节点，
        # 因为用户删除订阅时期望同时移除其下的所有节点
        self._get_conn().execute("DELETE FROM nodes WHERE subscription_id = ?", (sub_id,))
        self._get_conn().execute("DELETE FROM subscriptions WHERE id = ?", (sub_id,))
        self._get_conn().commit()

    # ---------- 路由规则 CRUD ----------
    def _deserialize_rule_rows(self, cursor) -> list:
        """Deserialize rule rows, parsing JSON array fields + snake_case → camelCase 转换"""
        rows = cursor.fetchall()
        result = []
        for row in rows:
            d = dict(zip([desc[0] for desc in cursor.description], row))
            for field in self._RULE_ARRAY_FIELDS:
                if field in d and isinstance(d[field], str):
                    try:
                        d[field] = json.loads(d[field])
                    except (json.JSONDecodeError, TypeError):
                        # fallback：JSON 解析失败时将原始字符串包装为数组，保持与前端 string[] 类型一致
                        # 注意：此路径下数组内容可能为无效文本，调用方需做业务校验
                        d[field] = [d[field]] if d[field] else []
            # snake_case → camelCase 转换，确保前端字段名一致
            d = self._convert_keys(d, self.RULE_KEY_MAP)
            result.append(d)
        return result

    def get_rules(self) -> list:
        cursor = self._get_conn().execute("SELECT * FROM routing_rules ORDER BY sort_order")
        return self._deserialize_rule_rows(cursor)

    def get_all_rules_raw(self) -> list:
        """获取所有规则（保留数据库原始 snake_case 键名，供内部 _build_config 使用）
        
        注意：get_all_rules() 返回 camelCase 键（经 _convert_keys 转换），
        但后端 _build_route 等方法需要使用数据库原始列名（snake_case）来匹配字段，
        因此必须使用此 raw 方法，否则 is_enabled→isEnabled、outbound_tag→outboundTag 等字段将无法匹配。
        """
        cursor = self._get_conn().execute("SELECT * FROM routing_rules ORDER BY sort_order")
        rows = cursor.fetchall()
        result = []
        for row in rows:
            d = dict(zip([desc[0] for desc in cursor.description], row))
            for field in self._RULE_ARRAY_FIELDS:
                if field in d and isinstance(d[field], str):
                    try:
                        d[field] = json.loads(d[field])
                    except (json.JSONDecodeError, TypeError):
                        # fallback：JSON 解析失败时将原始字符串包装为数组，保持与前端 string[] 类型一致
                        # 注意：此路径下数组内容可能为无效文本，调用方需做业务校验
                        d[field] = [d[field]] if d[field] else []
            result.append(d)
        return result

    def add_rule(self, data: dict) -> str:
        import uuid
        # 前端可能传入 camelCase 键名，自动转换为 snake_case
        data = self._convert_keys(data, self.RULE_KEY_MAP_REVERSE)
        rule_id = data.get('id', str(uuid.uuid4()))
        columns = ['id']
        values = [rule_id]
        for k in data.keys():
            if k in self.RULE_COLUMNS:
                columns.append(k)
                v = data[k]
                # 数组字段需要 JSON 序列化
                if k in self._RULE_ARRAY_FIELDS and isinstance(v, list):
                    v = json.dumps(v)
                values.append(v)
        placeholders = ", ".join("?" * len(columns))
        self._get_conn().execute(
            f"INSERT INTO routing_rules ({', '.join(columns)}) VALUES ({placeholders})",
            values
        )
        self._get_conn().commit()
        return rule_id

    def update_rule(self, rule_id: str, updates: dict):
        # 前端可能传入 camelCase 键名，自动转换为 snake_case
        updates = self._convert_keys(updates, self.RULE_KEY_MAP_REVERSE)
        # 数组字段序列化（使用类常量 _RULE_ARRAY_FIELDS）
        for field in self._RULE_ARRAY_FIELDS:
            if field in updates and isinstance(updates[field], list):
                updates[field] = json.dumps(updates[field])
        self._safe_update("routing_rules", self.RULE_COLUMNS, rule_id, updates)

    def delete_rule(self, rule_id: str):
        self._get_conn().execute("DELETE FROM routing_rules WHERE id = ?", (rule_id,))
        self._get_conn().commit()

    # ---------- Rule Set CRUD ----------
    RULE_SET_KEY_MAP = {
        'download_detour': 'downloadDetour',
        'is_enabled': 'isEnabled',
        'created_at': 'createdAt',
        'updated_at': 'updatedAt',
    }
    RULE_SET_KEY_MAP_REVERSE = {v: k for k, v in RULE_SET_KEY_MAP.items()}

    def get_rule_sets(self) -> list:
        cursor = self._get_conn().execute("SELECT * FROM rule_sets ORDER BY created_at")
        rows = [dict(zip([d[0] for d in cursor.description], row)) for row in cursor.fetchall()]
        return [self._convert_keys(r, self.RULE_SET_KEY_MAP) for r in rows]

    def get_all_rule_sets_raw(self) -> list:
        """获取所有规则集（保留数据库原始 snake_case 键名，供内部 _build_config 使用）
        
        注意：get_all_rule_sets() 返回 camelCase 键（经 _convert_keys 转换），
        但后端 _build_route 等方法需要使用数据库原始列名（snake_case）来匹配字段，
        例如 rs.get('is_enabled', 1) 需要 snake_case 键名才能正确过滤禁用的 rule_set。
        """
        cursor = self._get_conn().execute("SELECT * FROM rule_sets ORDER BY created_at")
        rows = [dict(zip([d[0] for d in cursor.description], row)) for row in cursor.fetchall()]
        return rows

    def add_rule_set(self, data: dict) -> str:
        # 前端传入 camelCase 键名，需转换为 snake_case（与其他 add 方法一致）
        data = self._convert_keys(data, self.RULE_SET_KEY_MAP_REVERSE)
        import uuid
        rs_id = data.get('id', str(uuid.uuid4()))
        self._get_conn().execute(
            "INSERT INTO rule_sets (id, name, tag, type, format, url, download_detour, is_enabled) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (rs_id, data['name'], data['tag'], data.get('type', 'remote'),
             data.get('format', 'binary'), data.get('url'), data.get('download_detour', 'proxy'), data.get('is_enabled', 1))
        )
        self._get_conn().commit()
        return rs_id

    def update_rule_set(self, rs_id: str, updates: dict):
        """更新规则集元数据（如 name, tag, url, is_enabled）"""
        # 前端可能传入 camelCase 键名，自动转换为 snake_case（与其他 update 方法一致）
        updates = self._convert_keys(updates, self.RULE_SET_KEY_MAP_REVERSE)
        safe_updates = {k: v for k, v in updates.items() if k in self.RULE_SET_COLUMNS}
        if not safe_updates:
            return
        self._safe_update("rule_sets", self.RULE_SET_COLUMNS, rs_id, safe_updates)

    def delete_rule_set(self, rs_id: str):
        self._get_conn().execute("DELETE FROM rule_sets WHERE id = ?", (rs_id,))
        self._get_conn().commit()

    # ---------- 设置 CRUD ----------
    def get_setting(self, key: str, default=None):
        result = self._get_conn().execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        if result is None:
            return default
        try:
            return json.loads(result[0])  # 所有值都由 json.dumps 存入，可直接 json.loads
        except (json.JSONDecodeError, TypeError):
            # 兼容旧版本数据（未用 json.dumps 存入的原始字符串）
            return result[0]

    def update_setting(self, key: str, value):
        # 统一使用 json.dumps 序列化所有类型（包括字符串），
        # 读取时统一 json.loads 反序列化，避免类型信息损坏。
        # 例：update_setting("k", "123") 存入 '"123"'（带引号的 JSON 字符串），
        # get_setting 读取时 json.loads('"123"') 正确返回字符串 "123"，
        # 而旧方案存入 123（无引号）导致 json.loads("123") 返回整数 123。
        self._get_conn().execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, datetime('now')) ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, json.dumps(value))
        )
        self._get_conn().commit()

    def get_settings(self) -> dict:
        cursor = self._get_conn().execute("SELECT key, value FROM app_settings")
        result = {}
        for row in cursor.fetchall():
            try:
                result[row[0]] = json.loads(row[1])
            except (json.JSONDecodeError, TypeError):
                result[row[0]] = row[1]
        return result

    def update_settings(self, settings: dict):
        """批量更新设置（使用事务保证原子性）
        注意：update_settings 使用单次 commit 保证批量写入的原子性。
        但如果同时从不同线程调用 update_setting（单键方法，自带 commit），
        可能出现中间状态（部分设置已提交，部分未提交）。
        实际场景中，设置更新通常由用户手动触发（串行），并发风险极低。
        如需更严格的隔离，可在调用层面加锁。
        """
        conn = self._get_conn()
        try:
            for key, value in settings.items():
                conn.execute(
                    "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, datetime('now')) ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                    (key, json.dumps(value))
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    # ---------- 连接清理 ----------
    # 全局连接追踪列表（实例级属性，__init__ 中初始化）
    _all_connections: list
    _all_connections_lock: threading.Lock
    _shutdown: bool  # 关闭标记，_get_conn() 检查此标记，避免 close_all() 后其他线程仍获取已关闭的连接

    def close(self):
        """关闭当前线程的数据库连接"""
        if hasattr(self._local, 'conn') and self._local.conn is not None:
            conn = self._local.conn
            self._local.conn = None
            with self._all_connections_lock:
                if conn in self._all_connections:
                    self._all_connections.remove(conn)
            conn.close()

    def close_all(self):
        """关闭所有线程的数据库连接（仅用于应用退出）

        遍历全局连接追踪列表关闭所有线程的连接，确保 SQLite WAL 锁及时释放。
        设置 _shutdown 标记后，其他线程的 _get_conn() 将不再返回新连接，
        避免关闭后其他线程的 thread-local 引用仍指向已关闭连接对象。
        """
        self._shutdown = True
        with self._all_connections_lock:
            for conn in list(self._all_connections):
                try:
                    conn.close()
                except Exception:
                    pass
            self._all_connections.clear()
        self.close()
        # 注意：其他线程的连接会在线程退出时自动关闭
        # 因为 QThread 结束后 thread-local 数据会被清理
        # _shutdown 标记确保在此期间不会有新连接被创建

    def __del__(self):
        self.close()
