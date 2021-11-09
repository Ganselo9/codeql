lgtm,codescanning
* `Class.isLocal` has been replaced with `ClassOrInterface.isLocal`. This is because as of Java 16, interfaces can be declared method-local. Accordingly, `LocalClassDeclStmt.getLocalClass` is renamed `LocalTypeDeclStmt.getLocalType` and  now returns a `ClassOrInterface`. `BusinessInterface`, declared in `EJB.qll`, has had its `isRemote` and `isLocal` methods renamed `isDeclaredLocal` and `isDeclaredRemote` to avoid a name clash.