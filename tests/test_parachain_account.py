from oracleservice.substrate_interface_utils import SubstrateInterfaceUtils


def test_show_correct_parachain_address():
    para_id = 1000
    ss58_format = 2
    para_addr = 'F7fq1jSNVTPfJmaHaXCMtatT1EZefCUsa7rRiQVNR5efcah'

    assert para_addr == SubstrateInterfaceUtils.get_parachain_address(para_id, ss58_format).ss58_address
