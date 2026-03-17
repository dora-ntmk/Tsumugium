import json
import discord

with open("messages.json", encoding="utf-8") as _f:
    _MESSAGES = json.load(_f)

_COLOR_MAP = {
    "green": discord.Color.green,
    "red":   discord.Color.red,
    "blue":  discord.Color.blue,
}


def get_desc(key: str) -> str:
    """ドット区切りのキー（例: "commands.join"）でコマンドdescriptionを取得する。"""
    node = _MESSAGES
    for part in key.split("."):
        node = node[part]
    return node


async def handle_os_error(ctx, e: OSError, cmd_name: str) -> None:
    print(f"OSError in {cmd_name}: {e}")
    try:
        await ctx.edit_original_response(embed=build_embed("error.os_error"))
    except Exception as inner:
        print(f"Failed to send OSError embed in {cmd_name}: {inner}")


def build_embed(key: str, **kwargs) -> discord.Embed:
    """
    ドット区切りのキー（例: "join.success"）でEmbedを生成する。
    kwargsはdescriptionのstr.format()に渡される。
    """
    node = _MESSAGES
    for part in key.split("."):
        node = node[part]
    title = node["title"]
    description = node.get("description", "").format(**kwargs)
    color = _COLOR_MAP[node["color"]]()
    embed = discord.Embed(title=title, description=description or None, color=color)
    for field in node.get("fields", []):
        embed.add_field(
            name=field["name"].format(**kwargs),
            value=field["value"].format(**kwargs),
            inline=field.get("inline", True)
        )
    return embed
