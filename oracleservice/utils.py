from substrateinterface.utils.ss58 import ss58_encode


def get_parachain_address(_para_id, ss58_format):
    prefix = b'para'
    para_addr = bytearray(prefix)
    para_addr.append(_para_id & 0xFF)
    _para_id = _para_id >> 8
    para_addr.append(_para_id & 0xFF)
    _para_id = _para_id >> 8
    para_addr.append(_para_id & 0xFF)
    
    return ss58_encode(para_addr.ljust(32, b'\0'), ss58_format=ss58_format)
