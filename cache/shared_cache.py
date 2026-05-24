"""两层缓存：L1进程内dict + L2文件持久化，线程安全"""
import json, time, copy, random, threading
from pathlib import Path
from datetime import date, timedelta

class SharedCache:
    TTL_CONFIG = {
        "quotes": {"l1": 1, "l2_days": 0},
        "orderbook": {"l1": 0, "l2_days": 0},
        "klines": {"l1": 300, "l2_days": 1},
        "fundamentals": {"l1": 86400, "l2_days": 3},
        "news": {"l1": 3600, "l2_days": 1},
        "research": {"l1": 3600, "l2_days": 3},
        "signal": {"l1": 300, "l2_days": 1},
        "indicators": {"l1": 300, "l2_days": 1},
    }
    _DEFAULT_TTL = {"l1": 300, "l2_days": 1}
    _MAX_L2_FILES = 1000

    def __init__(self, cache_dir: str = "~/.tradingagents/cache"):
        self._cache_dir = Path(cache_dir).expanduser()
        self._mem: dict[str, tuple] = {}
        self._lock = threading.RLock()
        self._cleanup_count = 0

    def _ttl_for(self, category: str) -> tuple[int, int]:
        cfg = self.TTL_CONFIG.get(category, self._DEFAULT_TTL)
        l1 = cfg["l1"]
        if l1 > 0:
            jitter = random.randint(-max(1, l1 // 20), max(1, l1 // 20))
            l1 = max(0, l1 + jitter)
        return l1, cfg["l2_days"]

    def _l2_path(self, category: str, code: str, day_offset: int = 0) -> Path:
        d = date.today() + timedelta(days=day_offset)
        return self._cache_dir / category / f"{code}_{d.strftime('%Y-%m-%d')}.json"

    def read(self, category: str, code: str):
        l1_ttl, l2_days = self._ttl_for(category)
        if l1_ttl == 0:
            return None
        key = f"{category}:{code}"
        with self._lock:
            if key in self._mem:
                data, ts = self._mem[key]
                if time.time() - ts < l1_ttl:
                    return copy.deepcopy(data)
        for day_off in range(l2_days + 1):
            path = self._l2_path(category, code, -day_off)
            if path.exists():
                try:
                    data = json.loads(path.read_text())
                    with self._lock:
                        self._mem[key] = (data, time.time())
                    return copy.deepcopy(data)
                except json.JSONDecodeError:
                    path.unlink(missing_ok=True)
        return None

    def write(self, category: str, code: str, data):
        l1_ttl, l2_days = self._ttl_for(category)
        if l1_ttl == 0:
            return
        key = f"{category}:{code}"
        with self._lock:
            self._mem[key] = (copy.deepcopy(data), time.time())
        path = self._l2_path(category, code)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, default=str))
        tmp.replace(path)
        self._cleanup_count += 1
        if self._cleanup_count >= 100:
            self._cleanup_count = 0
            self._cleanup_l2()

    def _cleanup_l2(self):
        for category in self.TTL_CONFIG:
            cache_dir = self._cache_dir / category
            if not cache_dir.exists():
                continue
            files = sorted(cache_dir.glob("*.json"), key=lambda f: f.stat().st_mtime)
            if len(files) > self._MAX_L2_FILES:
                for f in files[:len(files) - self._MAX_L2_FILES]:
                    f.unlink(missing_ok=True)

cache = SharedCache()
