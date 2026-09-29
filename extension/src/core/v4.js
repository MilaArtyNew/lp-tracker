// Сборка calldata для Uniswap v4 PositionManager.modifyLiquidities.
import { AbiCoder, Interface, ZeroAddress, getAddress, keccak256 } from "ethers";

export const Actions = {
  DECREASE_LIQUIDITY: 0x01, MINT_POSITION: 0x02, BURN_POSITION: 0x03,
  SETTLE_PAIR: 0x0d, TAKE_PAIR: 0x11, SWEEP: 0x14,
};
const coder = AbiCoder.defaultAbiCoder();
const POOL_KEY = "(address,address,uint24,int24,address)";

export const POSM_IFACE = new Interface([
  "function modifyLiquidities(bytes unlockData, uint256 deadline) payable",
  "function getPositionLiquidity(uint256 tokenId) view returns (uint128)",
  "function getPoolAndPositionInfo(uint256 tokenId) view returns ((address,address,uint24,int24,address) poolKey, uint256 info)",
  "function poolKeys(bytes25 poolId) view returns (address currency0, address currency1, uint24 fee, int24 tickSpacing, address hooks)",
  "function ownerOf(uint256 tokenId) view returns (address)",
  "function nextTokenId() view returns (uint256)",
]);
export const STATE_VIEW_IFACE = new Interface([
  "function getSlot0(bytes32 poolId) view returns (uint160 sqrtPriceX96, int24 tick, uint24 protocolFee, uint24 lpFee)",
]);
export const ERC20_IFACE = new Interface([
  "function approve(address,uint256) returns (bool)",
  "function allowance(address,address) view returns (uint256)",
  "function balanceOf(address) view returns (uint256)",
  "function decimals() view returns (uint8)",
  "function symbol() view returns (string)",
]);
export const PERMIT2 = "0x000000000022D473030F116dDEE9F6B43aC78BA3";
export const PERMIT2_IFACE = new Interface([
  "function approve(address token, address spender, uint160 amount, uint48 expiration)",
  "function allowance(address owner, address token, address spender) view returns (uint160 amount, uint48 expiration, uint48 nonce)",
]);

const enc = (types, values) => coder.encode(types, values);
const pack = (actions, params) => coder.encode(["bytes", "bytes[]"], [Uint8Array.from(actions), params]);
const modify = (posm, unlock, deadline, value = 0n) =>
  ({ to: getAddress(posm), data: POSM_IFACE.encodeFunctionData("modifyLiquidities", [unlock, deadline]), value });

export const poolKeyArray = (k) => [getAddress(k.currency0), getAddress(k.currency1), k.fee, k.tickSpacing, getAddress(k.hooks)];
export const isNative = (c) => c === ZeroAddress || c.toLowerCase() === ZeroAddress;

// Один MINT на диапазон + один SETTLE_PAIR (+ SWEEP нативного остатка): весь Grid — одна транзакция.
export function buildMintGrid({ posm, poolKey, ranges, recipient, deadline }) {
  if (!ranges.length) throw new Error("нет диапазонов");
  if (isNative(poolKey.currency1)) throw new Error("native не может быть currency1");
  const pk = poolKeyArray(poolKey);
  const actions = [], params = [];
  for (const r of ranges) {
    actions.push(Actions.MINT_POSITION);
    params.push(enc([POOL_KEY, "int24", "int24", "uint256", "uint128", "uint128", "address", "bytes"],
      [pk, r.tickLower, r.tickUpper, r.liquidity, r.amount0Max, r.amount1Max, getAddress(recipient), "0x"]));
  }
  actions.push(Actions.SETTLE_PAIR);
  params.push(enc(["address", "address"], [pk[0], pk[1]]));
  let value = 0n;
  if (isNative(poolKey.currency0)) {
    value = ranges.reduce((s, r) => s + r.amount0Max, 0n);
    actions.push(Actions.SWEEP);                     // вернуть неиспользованный ETH
    params.push(enc(["address", "address"], [ZeroAddress, getAddress(recipient)]));
  }
  return modify(posm, pack(actions, params), deadline, value);
}

// Собрать комиссии: DECREASE(0) + TAKE_PAIR
export function buildCollect({ posm, tokenId, currency0, currency1, recipient, deadline }) {
  return modify(posm, pack([Actions.DECREASE_LIQUIDITY, Actions.TAKE_PAIR], [
    enc(["uint256", "uint256", "uint128", "uint128", "bytes"], [tokenId, 0n, 0n, 0n, "0x"]),
    enc(["address", "address", "address"], [getAddress(currency0), getAddress(currency1), getAddress(recipient)]),
  ]), deadline);
}

// Закрыть позицию целиком: BURN (выводит всю ликвидность + комиссии и сжигает NFT) + TAKE_PAIR
export function buildClose({ posm, tokenId, currency0, currency1, recipient, amount0Min = 0n, amount1Min = 0n, deadline }) {
  return modify(posm, pack([Actions.BURN_POSITION, Actions.TAKE_PAIR], [
    enc(["uint256", "uint128", "uint128", "bytes"], [tokenId, amount0Min, amount1Min, "0x"]),
    enc(["address", "address", "address"], [getAddress(currency0), getAddress(currency1), getAddress(recipient)]),
  ]), deadline);
}

// info из getPoolAndPositionInfo: [poolId(200) | tickUpper(24) | tickLower(24) | hasSubscriber(8)]
export function decodePositionTicks(info) {
  const toInt24 = (x) => { x = Number(x & 0xffffffn); return x >= 0x800000 ? x - 0x1000000 : x; };
  return { tickLower: toInt24(info >> 8n), tickUpper: toInt24(info >> 32n) };
}

// poolId = keccak256(abi.encode(poolKey)); статический struct кодируется как 5 слов
export function poolIdOf(k) {
  return keccak256(coder.encode(["address", "address", "uint24", "int24", "address"], poolKeyArray(k)));
}
