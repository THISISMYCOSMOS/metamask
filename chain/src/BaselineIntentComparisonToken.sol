// SPDX-License-Identifier: MIT
pragma solidity ^0.8.23;

/// @notice Deterministic local research fixture, not a production asset.
contract BaselineIntentComparisonToken {
    uint8 public constant decimals = 6;
    mapping(address => uint256) public balanceOf;
    event Transfer(address indexed from, address indexed to, uint256 value);

    constructor(address holder, uint256 amount) {
        balanceOf[holder] = amount;
        emit Transfer(address(0), holder, amount);
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        require(balanceOf[msg.sender] >= amount, "insufficient balance");
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        emit Transfer(msg.sender, to, amount);
        return true;
    }
}
