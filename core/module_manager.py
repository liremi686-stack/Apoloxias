import os
import sys
import json
import importlib.util
from pathlib import Path
from typing import Dict, List, Optional, Type
from core.module_api import ModuleAPI, ModuleInterface, EventBus, ModuleManifest


class ModuleManager:
    def __init__(self, modules_dir: Path, app_reference, crypto_engine):
        self.modules_dir = modules_dir
        self._app = app_reference
        self._crypto = crypto_engine
        self._event_bus = EventBus()
        self._modules: Dict[str, ModuleInterface] = {}
        self._apis: Dict[str, ModuleAPI] = {}
        self._classes: Dict[str, Type[ModuleInterface]] = {}
        self._manifests: Dict[str, ModuleManifest] = {}

    def discover_modules(self) -> List[ModuleManifest]:
        manifests = []
        if not self.modules_dir.exists():
            return manifests
        for module_dir in self.modules_dir.iterdir():
            if not module_dir.is_dir():
                continue
            manifest = self._read_manifest(module_dir)
            if manifest:
                manifests.append(manifest)
                self._manifests[manifest.name] = manifest
        return manifests

    def _read_manifest(self, module_dir: Path) -> Optional[ModuleManifest]:
        main_file = module_dir / "main.py"
        manifest_file = module_dir / "manifest.json"
        if not main_file.exists():
            return None
        if manifest_file.exists():
            try:
                with open(manifest_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return ModuleManifest(
                    name=data.get("name", module_dir.name),
                    version=data.get("version", "1.0.0"),
                    author=data.get("author", "Unknown"),
                    description=data.get("description", ""),
                    icon=data.get("icon", "icon.ico"),
                    entry_point=data.get("entry_point", "main.py"),
                    requires=data.get("requires", []),
                    permissions=data.get("permissions", []),
                )
            except Exception:
                pass
        return ModuleManifest(name=module_dir.name, icon="icon.ico", entry_point="main.py")

    def load_module(self, module_name: str) -> bool:
        if module_name in self._modules:
            return True
        module_dir = self.modules_dir / module_name
        if not module_dir.exists():
            return False
        manifest = self._manifests.get(module_name)
        if not manifest:
            manifest = self._read_manifest(module_dir)
            if not manifest:
                return False
            self._manifests[module_name] = manifest
        main_file = module_dir / manifest.entry_point
        if not main_file.exists():
            return False
        try:
            if str(module_dir) not in sys.path:
                sys.path.insert(0, str(module_dir))
            spec = importlib.util.spec_from_file_location(f"apx_mod_{module_name}", main_file)
            if not spec or not spec.loader:
                return False
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod_class = None
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if isinstance(attr, type) and issubclass(attr, ModuleInterface) and attr is not ModuleInterface:
                    mod_class = attr
                    break
            if not mod_class:
                return False
            api = ModuleAPI(
                module_name=module_name,
                module_path=module_dir,
                event_bus=self._event_bus,
                app_reference=self._app,
                crypto_engine=self._crypto,
            )
            instance = mod_class(api)
            instance.on_load()
            self._classes[module_name] = mod_class
            self._modules[module_name] = instance
            self._apis[module_name] = api
            return True
        except Exception as e:
            print(f"[ModuleManager] Load error '{module_name}': {e}")
            import traceback
            traceback.print_exc()
            return False

    def unload_module(self, module_name: str):
        if module_name not in self._modules:
            return
        try:
            self._modules[module_name].on_unload()
        except Exception as e:
            print(f"[ModuleManager] Unload error '{module_name}': {e}")
        del self._modules[module_name]
        del self._apis[module_name]
        if module_name in self._classes:
            del self._classes[module_name]

    def get_module(self, module_name: str) -> Optional[ModuleInterface]:
        return self._modules.get(module_name)

    def get_manifest(self, module_name: str) -> Optional[ModuleManifest]:
        return self._manifests.get(module_name)

    def get_loaded_modules(self) -> List[str]:
        return list(self._modules.keys())

    def get_all_modules(self) -> List[ModuleManifest]:
        return list(self._manifests.values())

    def unload_all(self):
        for name in list(self._modules.keys()):
            self.unload_module(name)
