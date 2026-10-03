// GHSA-86w9-cpqp-85rv: exercise actual RSA verification, not just ASN.1 parsing.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const forge = require('node-forge');
const { asn1 } = forge;
const keys = forge.pki.rsa.generateKeyPair({ bits: 1024, e: 3 });
const hash = () => forge.md.sha256.create().update('local regression fixture');
const primitive = (type, value) =>
  asn1.create(asn1.Class.UNIVERSAL, type, false, value);
const sequence = value =>
  asn1.create(asn1.Class.UNIVERSAL, asn1.Type.SEQUENCE, true, value);

function signature(parameters) {
  const info = sequence([
    sequence([
      primitive(asn1.Type.OID, asn1.oidToDer(forge.oids.sha256).getBytes()),
      ...parameters,
    ]),
    primitive(asn1.Type.OCTETSTRING, hash().digest().getBytes()),
  ]);
  return keys.privateKey.sign(asn1.toDer(info).getBytes(), 'NONE');
}

test('valid RSA signatures accept SHA256 OID with optional NULL', () => {
  for (const parameters of [[], [primitive(asn1.Type.NULL, '')]]) {
    assert.equal(
      keys.publicKey.verify(hash().digest().getBytes(), signature(parameters)),
      true,
    );
  }
  assert.equal(
    keys.publicKey.verify(
      hash().digest().getBytes(),
      keys.privateKey.sign(hash()),
    ),
    true,
  );
});

for (const parameters of [
  [primitive(asn1.Type.NULL, ''), primitive(asn1.Type.OCTETSTRING, 'garbage')],
  [primitive(asn1.Type.OCTETSTRING, 'garbage')],
  [primitive(asn1.Type.NULL, ''), primitive(asn1.Type.NULL, '')],
]) {
  test(`reject extra DigestAlgorithm children: ${parameters
    .map(p => p.type)
    .join(',')}`, () => {
    assert.throws(
      () =>
        keys.publicKey.verify(
          hash().digest().getBytes(),
          signature(parameters),
        ),
      /valid RSASSA-PKCS1-v1_5 DigestInfo/,
    );
  });
}
