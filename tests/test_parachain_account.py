from oracleservice.substrate_interface_utils import SubstrateInterfaceUtils
from substrateinterface import Keypair


def test_show_correct_parachain_address():
    para_id = 1000
    ss58_format = 2

    para_addr_expected = 'F7fq1jSNVTPfJmaHaXCMtatT1EZefCUsa7rRiQVNR5efcah'
    para_addr_actual = Keypair(public_key=SubstrateInterfaceUtils.get_parachain_address(para_id), ss58_format=ss58_format)
    
    assert para_addr_expected == para_addr_actual.ss58_address
