"""
Domain Configuration Loader
Загружает конфигурацию домена (textile_manufacturing или ai_seller_self)
"""

import os
import yaml
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
from enum import Enum


class Domain(str, Enum):
    """Доступные домены"""
    TEXTILE_MANUFACTURING = "textile_manufacturing"
    AI_SELLER_SELF = "ai_seller_self"


@dataclass
class DomainConfig:
    """Конфигурация домена"""
    domain: str
    version: str
    language: str
    active: bool
    description: str

    # Loaded configs
    slots: Dict[str, Any] = field(default_factory=dict)
    states: Dict[str, Any] = field(default_factory=dict)
    business_rules: Dict[str, Any] = field(default_factory=dict)
    system_prompt: str = ""

    # Paths
    domain_path: Path = None


class DomainLoader:
    """Загрузчик конфигураций доменов"""

    def __init__(self, base_path: Optional[Path] = None):
        if base_path is None:
            # Default: config/domains/
            self.base_path = Path(__file__).parent.parent / "config" / "domains"
        else:
            self.base_path = Path(base_path)

    def load_domain(self, domain_name: str) -> DomainConfig:
        """
        Загружает конфигурацию домена

        Args:
            domain_name: Имя домена (textile_manufacturing, ai_seller_self)

        Returns:
            DomainConfig object

        Raises:
            FileNotFoundError: Если домен не найден
            ValueError: Если конфигурация невалидна
        """
        domain_path = self.base_path / domain_name

        if not domain_path.exists():
            raise FileNotFoundError(
                f"Domain '{domain_name}' not found at {domain_path}"
            )

        # Load domain.yaml
        domain_yaml = domain_path / "domain.yaml"
        if not domain_yaml.exists():
            raise FileNotFoundError(f"domain.yaml not found in {domain_path}")

        with open(domain_yaml, 'r', encoding='utf-8') as f:
            domain_data = yaml.safe_load(f)

        # Create config object
        config = DomainConfig(
            domain=domain_data.get('domain', domain_name),
            version=domain_data.get('version', '1.0.0'),
            language=domain_data.get('language', 'ru'),
            active=domain_data.get('active', True),
            description=domain_data.get('description', ''),
            domain_path=domain_path
        )

        # Check if domain is active
        if not config.active:
            raise ValueError(f"Domain '{domain_name}' is not active")

        # Load slots
        slots_yaml = domain_path / "slots.yaml"
        if slots_yaml.exists():
            with open(slots_yaml, 'r', encoding='utf-8') as f:
                config.slots = yaml.safe_load(f)

        # Load states
        states_yaml = domain_path / "states.yaml"
        if states_yaml.exists():
            with open(states_yaml, 'r', encoding='utf-8') as f:
                config.states = yaml.safe_load(f)

        # Load business rules
        rules_yaml = domain_path / "business_rules.yaml"
        if rules_yaml.exists():
            with open(rules_yaml, 'r', encoding='utf-8') as f:
                config.business_rules = yaml.safe_load(f)

        # Load system prompt
        system_prompt_md = domain_path / "prompts" / "system_prompt.md"
        if system_prompt_md.exists():
            with open(system_prompt_md, 'r', encoding='utf-8') as f:
                config.system_prompt = f.read()

        return config

    def get_active_domain(self) -> str:
        """
        Получить активный домен из переменной окружения

        Returns:
            Имя активного домена (по умолчанию: textile_manufacturing)
        """
        domain = os.getenv('AI_SELLER_DOMAIN', Domain.TEXTILE_MANUFACTURING.value)

        # Validate
        if domain not in [d.value for d in Domain]:
            raise ValueError(
                f"Invalid domain '{domain}'. "
                f"Available: {[d.value for d in Domain]}"
            )

        return domain

    def list_available_domains(self) -> list[str]:
        """Список доступных доменов"""
        if not self.base_path.exists():
            return []

        domains = []
        for item in self.base_path.iterdir():
            if item.is_dir() and (item / "domain.yaml").exists():
                domains.append(item.name)

        return domains


# Singleton instance
_domain_loader = None
_current_domain_config = None


def get_domain_loader() -> DomainLoader:
    """Get global domain loader instance"""
    global _domain_loader
    if _domain_loader is None:
        _domain_loader = DomainLoader()
    return _domain_loader


def get_current_domain_config() -> DomainConfig:
    """
    Get current active domain configuration

    Returns:
        DomainConfig for active domain
    """
    global _current_domain_config

    if _current_domain_config is None:
        loader = get_domain_loader()
        active_domain = loader.get_active_domain()
        _current_domain_config = loader.load_domain(active_domain)

    return _current_domain_config


def reload_domain_config():
    """Reload domain configuration (e.g., after env change)"""
    global _current_domain_config
    _current_domain_config = None
    return get_current_domain_config()


def switch_domain(domain_name: str) -> DomainConfig:
    """
    Switch to different domain

    Args:
        domain_name: Domain to switch to

    Returns:
        New domain config
    """
    global _current_domain_config

    loader = get_domain_loader()
    _current_domain_config = loader.load_domain(domain_name)

    # Update env var
    os.environ['AI_SELLER_DOMAIN'] = domain_name

    return _current_domain_config


# Utility functions
def get_current_domain_name() -> str:
    """Get current domain name"""
    config = get_current_domain_config()
    return config.domain


def is_textile_mode() -> bool:
    """Check if running in textile manufacturing mode"""
    return get_current_domain_name() == Domain.TEXTILE_MANUFACTURING.value


def is_ai_seller_mode() -> bool:
    """Check if running in AI seller self mode"""
    return get_current_domain_name() == Domain.AI_SELLER_SELF.value


if __name__ == "__main__":
    # Test
    loader = DomainLoader()
    print("Available domains:", loader.list_available_domains())

    print(f"\nActive domain: {loader.get_active_domain()}")

    # Load config
    config = get_current_domain_config()
    print(f"\nLoaded domain: {config.domain} v{config.version}")
    print(f"Description: {config.description}")
    print(f"Language: {config.language}")
    print(f"Slots defined: {len(config.slots.get('slots', []))}")
    print(f"States defined: {len(config.states.get('states', []))}")
    print(f"System prompt length: {len(config.system_prompt)} chars")
