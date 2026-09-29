import { CHAINS, provider, positionState } from "../src/core/chain.js";
import { POSM_IFACE } from "../src/core/v4.js";

// Живая позиция «зрелого» возраста: самые свежие NFT часто сжигаются через минуты и делают тест нестабильным.
export async function findLivePosition(chain, { skip = 3000, span = 2500 } = {}) {
  const posm = CHAINS[chain].posm;
  const [next] = POSM_IFACE.decodeFunctionResult("nextTokenId",
    await provider(chain).call({ to: posm, data: POSM_IFACE.encodeFunctionData("nextTokenId") }));
  for (let id = next - BigInt(skip); id > next - BigInt(skip + span); id--) {
    try { const st = await positionState(chain, id); if (st.liquidity > 0n) return { id, st }; } catch { /* сожжена */ }
  }
  return null;
}
