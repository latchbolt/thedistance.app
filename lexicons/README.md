# Lexicons

AT Protocol lexicon schemas for The Distance. Published to the network via [goat](https://github.com/bluesky-social/indigo/tree/main/cmd/goat).

Viewable on [Lexicon Garden](https://lexicon.garden/identity/did:plc:x52h4ttzfk5rxxdmzinoevgo).

## Publishing

After making changes to a lexicon file, publish it to the network:

```
goat lex publish --update --username <handle> --password <app-password>
```

Or with `GOAT_USERNAME` and `GOAT_PASSWORD` env vars set:

```
goat lex publish --update
```

### Useful commands

Check what's changed locally vs. the live network:

```
goat lex diff
```

Validate schema files without publishing:

```
goat lex lint
```

Check DNS configuration for NSIDs:

```
goat lex check-dns
```
