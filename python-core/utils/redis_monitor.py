"""
Redis monitoring utility for SoVAni AI Seller
"""

import redis
import os
from typing import Dict, Any
import structlog
from dotenv import load_dotenv

load_dotenv()

logger = structlog.get_logger("ai_seller.redis_monitor")

class RedisMonitor:
    def __init__(self):
        self.primary_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.backup_url = os.getenv("REDIS_BACKUP_URL")
        
    def get_primary_stats(self) -> Dict[str, Any]:
        """Get primary Redis stats"""
        try:
            client = redis.from_url(self.primary_url)
            info = client.info()
            return {
                "status": "connected",
                "used_memory": info.get("used_memory_human"),
                "connected_clients": info.get("connected_clients"),
                "total_commands_processed": info.get("total_commands_processed"),
                "keyspace_hits": info.get("keyspace_hits"),
                "keyspace_misses": info.get("keyspace_misses")
            }
        except Exception as e:
            logger.error("primary_redis_error", error=str(e))
            return {"status": "error", "error": str(e)}
    
    def test_backup_connection(self) -> Dict[str, Any]:
        """Test backup Redis connection"""
        if not self.backup_url:
            return {"status": "not_configured"}
            
        try:
            client = redis.from_url(self.backup_url)
            client.ping()
            info = client.info()
            return {
                "status": "connected",
                "used_memory": info.get("used_memory_human"),
                "maxmemory": info.get("maxmemory_human")
            }
        except Exception as e:
            logger.error("backup_redis_error", error=str(e))
            return {"status": "error", "error": str(e)}

    def health_check(self) -> Dict[str, Any]:
        """Complete Redis health check"""
        return {
            "primary": self.get_primary_stats(),
            "backup": self.test_backup_connection(),
            "timestamp": "now"
        }

if __name__ == "__main__":
    monitor = RedisMonitor()
    print("Redis Health Check:")
    print(monitor.health_check())