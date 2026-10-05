package com.day.cq.wcm.commons
class ReferenceSearch {
    static Closure handler
    void setExact(boolean b) {}
    void setMaxReferencesPerPage(int n) {}
    Map search(Object resolver, String path) { handler(path) }
}
