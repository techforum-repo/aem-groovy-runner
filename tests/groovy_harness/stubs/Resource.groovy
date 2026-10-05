package org.apache.sling.api.resource
interface ValueMap extends Map<String,Object> {
    def <T> T get(String name, Class<T> type)
    def <T> T get(String name, T defaultValue)
}
class SimpleValueMap extends HashMap<String,Object> implements ValueMap {
    SimpleValueMap(Map m) { super(m) }
    def <T> T get(String name, Class<T> type) { def v = super.get(name); type.isInstance(v) ? (T) v : null }
    def <T> T get(String name, T dflt) { def v = super.get(name); v == null ? dflt : (T) v }
}
interface Resource {
    String getPath()
    Resource getChild(String rel)
    def <T> T adaptTo(Class<T> type)
}
