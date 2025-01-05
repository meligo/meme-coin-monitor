from construct import Flag, Int64ul, Struct


class BondingCurveState:
    """Represents the state of a bonding curve"""
    
    _STRUCT = Struct(
        "virtual_token_reserves" / Int64ul,
        "virtual_sol_reserves" / Int64ul,
        "real_token_reserves" / Int64ul,
        "real_sol_reserves" / Int64ul,
        "token_total_supply" / Int64ul,
        "complete" / Flag
    )

    def __init__(self, data: bytes) -> None:
        parsed = self._STRUCT.parse(data[8:])
        self.__dict__.update(parsed)

    def __str__(self):
        return (
            f"BondingCurveState("
            f"virtual_token_reserves={self.virtual_token_reserves}, "
            f"virtual_sol_reserves={self.virtual_sol_reserves}, "
            f"real_token_reserves={self.real_token_reserves}, "
            f"real_sol_reserves={self.real_sol_reserves}, "
            f"token_total_supply={self.token_total_supply}, "
            f"complete={self.complete})"
        )