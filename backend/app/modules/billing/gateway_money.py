"""Convert gateway amounts using the storefront's currency precision."""

from decimal import Decimal, InvalidOperation

# Currency digits match Intl.NumberFormat in the storefront and admin UI.
_ZERO = set("ADP AFN ALL BIF BYR CLP COP DJF ESP GNF HUF IDR IQD IRR ISK ITL JPY KMF KPW KRW LAK LBP LUF MGA MGF MMK MRO PKR PYG RSD RWF SLL SOS STD SYP TMM TRL UGX UYI VND VUV XAF XOF XPF YER ZMK ZWD".split())
_THREE = {"BHD", "JOD", "KWD", "LYD", "OMR", "TND"}
_FOUR = {"CLF", "UYW"}


def currency_digits(currency):
    if currency in _ZERO:
        return 0
    if currency in _THREE:
        return 3
    if currency in _FOUR:
        return 4
    return 2


def as_minor(amount, currency, *, allow_zero=False):
    try:
        value = Decimal(str(amount))
        if value.is_finite() and (value > 0 or allow_zero and value == 0) and value <= Decimal("99999999"):
            digits = currency_digits(currency)
            if value == value.quantize(Decimal(1).scaleb(-digits)):
                return int(value.scaleb(digits))
    except (InvalidOperation, ValueError):
        pass
    raise ValueError("Gateway amount does not match the currency precision")


def as_major(amount, currency):
    digits = currency_digits(currency)
    return format(Decimal(amount).scaleb(-digits), f".{digits}f")
