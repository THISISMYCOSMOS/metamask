// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice Minimal local-only ERC-20 fixture for the gate-position experiment.
contract GatePositionToken {
    string public constant name = "Gate Position Test Token";
    string public constant symbol = "GPTT";
    uint8 public constant decimals = 6;

    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    constructor(address owner, address spender, uint256 initialBalance) {
        balanceOf[owner] = initialBalance;
        allowance[owner][spender] = initialBalance;
        emit Transfer(address(0), owner, initialBalance);
        emit Approval(owner, spender, initialBalance);
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        emit Approval(msg.sender, spender, amount);
        return true;
    }

    function transfer(address recipient, uint256 amount) external returns (bool) {
        _transfer(msg.sender, recipient, amount);
        return true;
    }

    function transferFrom(address owner, address recipient, uint256 amount) external returns (bool) {
        uint256 currentAllowance = allowance[owner][msg.sender];
        require(currentAllowance >= amount, "insufficient allowance");
        allowance[owner][msg.sender] = currentAllowance - amount;
        _transfer(owner, recipient, amount);
        return true;
    }

    function _transfer(address from, address recipient, uint256 amount) private {
        uint256 currentBalance = balanceOf[from];
        require(currentBalance >= amount, "insufficient balance");
        balanceOf[from] = currentBalance - amount;
        balanceOf[recipient] += amount;
        emit Transfer(from, recipient, amount);
    }
}
