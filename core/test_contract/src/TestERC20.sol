// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/// @notice Minimal, deliberately dependency-free ERC-20 used by local Anvil tests.
contract TestERC20 {
    string public constant name = "Controlled Test Token";
    string public constant symbol = "CTT";
    uint8 public constant decimals = 18;
    uint256 public totalSupply;

    mapping(address => uint256) public balanceOf;

    event Transfer(address indexed from, address indexed to, uint256 value);

    constructor(uint256 initialSupply) {
        totalSupply = initialSupply;
        balanceOf[msg.sender] = initialSupply;
        emit Transfer(address(0), msg.sender, initialSupply);
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        require(to != address(0), "zero recipient");
        uint256 fromBalance = balanceOf[msg.sender];
        require(fromBalance >= amount, "insufficient balance");
        unchecked {
            balanceOf[msg.sender] = fromBalance - amount;
            balanceOf[to] += amount;
        }
        emit Transfer(msg.sender, to, amount);
        return true;
    }
}
