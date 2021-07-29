from oracleservice.utils import get_parachain_address

def test_show_correct_parachain_address():
    para_id = 1000
    ss58_format = 2
    para_addr = 'F7fq1jSNVTPfJmaHaXCMtatT1EZefCUsa7rRiQVNR5efcah'

    assert para_addr == get_parachain_address(para_id, ss58_format)
