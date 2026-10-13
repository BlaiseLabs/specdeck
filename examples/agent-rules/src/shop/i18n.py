"""Messages shown to people, in each language the shop supports."""
MESSAGES = {
    "en": {
        "order.created": "Order {id} created for {customer}: {total}",
        "order.missing": "No order {id}",
        "order.listed": "{id}  {customer}  {total}  {status}",
    },
    "ja": {
        "order.created": "{customer}様の注文{id}を作成しました：{total}",
        "order.missing": "注文{id}はありません",
        "order.listed": "{id}  {customer}  {total}  {status}",
    },
}


def t(key: str, lang: str = "en", **values) -> str:
    """The message `key` in `lang`, with `values` filled in."""
    return MESSAGES[lang][key].format(**values)
