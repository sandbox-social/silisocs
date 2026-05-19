"""Game-master extension package."""

__all__ = [
    "AppInitializeGameMasterInitializer",
    "BaseGameMaster",
    "FlowRoutedGameMaster",
    "GameMaster",
    "GameMasterInitializer",
    "NoOpGameMasterInitializer",
    "SocialMediaGameMasterInitializer",
    "build_game_master_initializer",
]


def __getattr__(name: str):
    if name == "BaseGameMaster":
        from silisocs.environments.gm.base_game_master import BaseGameMaster

        return BaseGameMaster
    if name == "GameMaster":
        from silisocs.environments.gm.game_master import GameMaster

        return GameMaster
    if name == "FlowRoutedGameMaster":
        from silisocs.environments.gm.shared_flow_game_master import FlowRoutedGameMaster

        return FlowRoutedGameMaster
    if name in {
        "AppInitializeGameMasterInitializer",
        "GameMasterInitializer",
        "NoOpGameMasterInitializer",
        "SocialMediaGameMasterInitializer",
        "build_game_master_initializer",
    }:
        from silisocs import initialization
        from silisocs.initialization import game_masters

        del initialization
        return getattr(game_masters, name)
    raise AttributeError(name)
